"""The simulated processor's tariff.

This is an independent implementation of "what should this transaction be
charged". It reads the published rates from config/fee_rules.json as raw JSON,
by rule_id, and decides applicability with its own code.

It deliberately does not import mfp.rules, mfp.fees or mfp.proof (enforced by
tools/check_firewall.py). The Fee Engine built in Block 2 is a second,
separate implementation. When the two agree to the paise on a planted
discrepancy, that agreement is evidence; if they shared code it would be an
echo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from mfp.core.enums import Instrument, MerchantClass, RoundingPolicy
from mfp.core.money import Money, Rate, apply_rate

CARD_INSTRUMENTS = frozenset({Instrument.CARD_CREDIT, Instrument.CARD_DEBIT, Instrument.RUPAY_DEBIT})
UPI_BANK_INSTRUMENTS = frozenset({Instrument.UPI_P2M_BANK, Instrument.UPI_LITE})


@dataclass(frozen=True, slots=True)
class Charges:
    mdr: int = 0
    gst: int = 0
    tcs: int = 0
    tds: int = 0
    mdr_rule: str | None = None
    gst_rule: str | None = None

    @property
    def total(self) -> int:
        return self.mdr + self.gst + self.tcs + self.tds


@dataclass(frozen=True, slots=True)
class RateCard:
    """The contract-rate portion of pricing, from an agreement or a processor snapshot."""

    card_credit: Decimal
    card_debit: Decimal
    netbanking: Decimal


def _d(value: str) -> date:
    return date.fromisoformat(value)


class ProcessorTariff:
    def __init__(self, fee_rules: dict) -> None:
        rules = {r["rule_id"]: r for r in fee_rules["rules"]}
        self._rules = rules
        rounding = fee_rules["rounding_rules"][0]["policy"]
        self.rounding = RoundingPolicy(rounding)

        upto = rules["MDR.UPI_P2M.NIL.UPTO_2000"]
        # max_paise is exclusive, so the protected ceiling is one paise below it.
        self.small_value_ceiling = upto["scope"]["amount_range"]["max_paise"] - 1
        self.small_value_protection_from = _d(upto["effective_from"])
        self.legacy_nil_until = _d(rules["MDR.UPI_P2M.NIL.LEGACY"]["effective_to"])

        std = rules["MDR.UPI_P2M.STANDARD.ABOVE_2000"]
        self.upi_mdr_from = _d(std["effective_from"])
        self.upi_mdr_rate = Rate.from_percent(std["value_percent"])
        self.upi_mdr_cap = std["cap_paise"]

        essential = rules["MDR.UPI_P2M.ESSENTIAL.FLAT"]
        self.essential_mccs = frozenset(essential["scope"]["mccs"])
        self.essential_fee = essential["value_paise"]
        cap_mkts = rules["MDR.UPI_P2M.CAPITAL_MARKETS"]
        self.capital_market_mccs = frozenset(cap_mkts["scope"]["mccs"])
        self.capital_market_rate = Rate.from_percent(cap_mkts["value_percent"])
        self.capital_market_cap = cap_mkts["cap_paise"]

        default = rules["MDR.RUPAY_CC_UPI.DEFAULT"]
        self.rupay_cc_from = _d(default["effective_from"])
        self.rupay_cc_default = (Rate.from_percent(default["value_percent"]), default["rule_id"])
        self.rupay_cc_by_mcc: dict[str, tuple[Rate, str]] = {}
        for rule in rules.values():
            if rule["rule_id"].startswith("MDR.RUPAY_CC_UPI.") and rule["scope"].get("mccs"):
                for mcc in rule["scope"]["mccs"]:
                    self.rupay_cc_by_mcc[mcc] = (Rate.from_percent(rule["value_percent"]), rule["rule_id"])

        ppi = rules["MDR.PPI_ON_UPI.INTERCHANGE"]
        self.ppi_rate = Rate.from_percent(ppi["value_percent"])

        gst = rules["GST.MDR.STANDARD"]
        self.gst_rate = Rate.from_percent(gst["value_percent"])
        exempt = rules["GST.EXEMPT.PA_SETTLEMENT_UPTO_2000"]
        self.gst_exempt_instruments = frozenset(Instrument(i) for i in exempt["scope"]["instruments"])
        self.gst_exempt_ceiling = exempt["scope"]["amount_range"]["max_paise"] - 1
        self.gst_exempt_from = _d(exempt["effective_from"])

        tcs = rules["TCS.ECO.CGST_52"]
        self.tcs_rate = Rate.from_percent(tcs["value_percent"])
        self.tcs_from = _d(tcs["effective_from"])
        # RBI debit-card ceilings by previous-year turnover band.
        self.debit_caps = []
        for rule in rules.values():
            if rule["rule_type"] == "MDR_CAP":
                band = rule["scope"]["turnover_range"]
                self.debit_caps.append((band.get("min_paise", 0), band.get("max_paise"),
                                        Rate.from_percent(rule["value_percent"]), rule["cap_paise"], rule["rule_id"]))
        large = next(c for c in self.debit_caps if c[1] is None)
        self.large_debit_rate, self.large_debit_cap = large[2], large[3]

        tds = rules["TDS.ECO.194O"]
        self.tds_rate = Rate.from_percent(tds["value_percent"])
        self.tds_from = _d(tds["effective_from"])

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> ProcessorTariff:
        raw = json.loads((config_dir / "fee_rules.json").read_text(encoding="utf-8"))
        return cls(raw)

    # -- primitives ------------------------------------------------------

    def pct(self, amount_paise: int, rate: Rate) -> int:
        return apply_rate(Money(amount_paise), rate, self.rounding).paise

    def rupay_cc_rate(self, mcc: str) -> tuple[Rate, str]:
        return self.rupay_cc_by_mcc.get(mcc, self.rupay_cc_default)

    def debit_ceiling(self, amount_paise: int, turnover_paise: int | None) -> tuple[int, str] | None:
        if turnover_paise is None:
            return None
        for low, high, rate, cap, rule_id in self.debit_caps:
            if turnover_paise >= low and (high is None or turnover_paise < high):
                return min(self.pct(amount_paise, rate), cap), rule_id
        return None

    def upi_nil_rule(self, amount_paise: int, on: date) -> str:
        if on >= self.small_value_protection_from and amount_paise <= self.small_value_ceiling:
            return "MDR.UPI_P2M.NIL.UPTO_2000"
        return "MDR.UPI_P2M.NIL.LEGACY"

    # -- the correct charge ----------------------------------------------

    def mdr(
        self,
        instrument: Instrument,
        amount_paise: int,
        on: date,
        *,
        mcc: str,
        rates: RateCard,
        upi_class: MerchantClass,
        turnover: int | None = None,
    ) -> tuple[int, str | None]:
        """Return (mdr_paise, rule_id or agreement marker)."""
        if instrument in UPI_BANK_INSTRUMENTS:
            if on <= self.legacy_nil_until or amount_paise <= self.small_value_ceiling:
                return 0, self.upi_nil_rule(amount_paise, on)
            if upi_class is MerchantClass.P2PM:
                return 0, "MDR.UPI_P2M.P2PM.NIL"
            if instrument is Instrument.UPI_LITE:
                return 0, "MDR.UPI_P2M.NIL.UPTO_2000"
            if mcc in self.essential_mccs:
                return self.essential_fee, "MDR.UPI_P2M.ESSENTIAL.FLAT"
            if mcc in self.capital_market_mccs:
                return (
                    min(self.pct(amount_paise, self.capital_market_rate), self.capital_market_cap),
                    "MDR.UPI_P2M.CAPITAL_MARKETS",
                )
            return (
                min(self.pct(amount_paise, self.upi_mdr_rate), self.upi_mdr_cap),
                "MDR.UPI_P2M.STANDARD.ABOVE_2000",
            )

        if instrument is Instrument.RUPAY_DEBIT:
            return 0, "MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS"

        if instrument is Instrument.RUPAY_CC_ON_UPI:
            if on < self.rupay_cc_from:
                # No sourced rule before 1 Jun 2026; the clean processor charged nothing.
                return 0, None
            if amount_paise <= self.small_value_ceiling:
                return 0, "MDR.RUPAY_CC_UPI.NIL.UPTO_2000"
            rate, rule_id = self.rupay_cc_rate(mcc)
            return self.pct(amount_paise, rate), rule_id

        if instrument is Instrument.PPI_ON_UPI:
            # Clean world: no merchant-facing pass-through (see FEE_RULES.md section 7).
            return 0, None

        contract = {
            Instrument.CARD_CREDIT: rates.card_credit,
            Instrument.CARD_DEBIT: rates.card_debit,
            Instrument.NETBANKING: rates.netbanking,
        }[instrument]
        contracted = self.pct(amount_paise, Rate.from_percent(contract))
        if instrument is Instrument.CARD_DEBIT:
            ceiling = self.debit_ceiling(amount_paise, turnover)
            if ceiling is not None and ceiling[0] < contracted:
                return ceiling
        return contracted, "AGREEMENT"

    def gst(self, instrument: Instrument, amount_paise: int, mdr_paise: int, on: date) -> tuple[int, str | None]:
        if mdr_paise == 0:
            return 0, None
        if (
            instrument in self.gst_exempt_instruments
            and amount_paise <= self.gst_exempt_ceiling
            and on >= self.gst_exempt_from
        ):
            return 0, "GST.EXEMPT.PA_SETTLEMENT_UPTO_2000"
        return self.pct(mdr_paise, self.gst_rate), "GST.MDR.STANDARD"

    def taxes(self, gross_paise: int, on: date, *, is_ecommerce_participant: bool) -> tuple[int, int]:
        """(tcs, tds). Base is gross value -- a declared simplification, FEE_RULES.md section 5."""
        if not is_ecommerce_participant:
            return 0, 0
        tcs = self.pct(gross_paise, self.tcs_rate) if on >= self.tcs_from else 0
        tds = self.pct(gross_paise, self.tds_rate) if on >= self.tds_from else 0
        return tcs, tds

    def correct_charges(
        self,
        instrument: Instrument,
        amount_paise: int,
        on: date,
        *,
        mcc: str,
        rates: RateCard,
        upi_class: MerchantClass,
        is_ecommerce_participant: bool,
        turnover: int | None = None,
    ) -> Charges:
        mdr, mdr_rule = self.mdr(instrument, amount_paise, on, mcc=mcc, rates=rates, upi_class=upi_class,
                                 turnover=turnover)
        gst, gst_rule = self.gst(instrument, amount_paise, mdr, on)
        tcs, tds = self.taxes(amount_paise, on, is_ecommerce_participant=is_ecommerce_participant)
        return Charges(mdr=mdr, gst=gst, tcs=tcs, tds=tds, mdr_rule=mdr_rule, gst_rule=gst_rule)
