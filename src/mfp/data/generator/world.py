"""Merchants, contracts, processor configuration history and the transaction ledger.

Randomness is split into independent streams per merchant -- ledger, faults,
processor behaviour, settlement, bank -- so that turning leakage on or off
changes ONLY the fault stream. A clean baseline and a leaky dataset built from
the same seed therefore contain the identical transaction ledger, which is what
makes the baseline a controlled comparison rather than a different dataset.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from mfp.core.clock import IST
from mfp.core.enums import Instrument, MerchantClass
from mfp.data.generator.params import (
    ACQ_B_UPI_MDR_EARLY_FROM,
    ACQ_C_RUPAY_DEBIT_FROM,
    ACQUIRERS,
    CARD_CREDIT_BPS,
    CARD_DEBIT_BPS,
    CITIES,
    COHORT_MCCS,
    HERO_ACQUIRER,
    INSTRUMENT_MIX,
    NETBANKING_BPS,
    RUPAY_CC_TABLE_LOADED_ON,
    GenerationParams,
)
from mfp.data.generator.tariff import ProcessorTariff, RateCard
from mfp.data.generator.truth import Fault, FaultProfile

CARD_LIKE = frozenset(
    {Instrument.CARD_CREDIT, Instrument.CARD_DEBIT, Instrument.RUPAY_DEBIT, Instrument.RUPAY_CC_ON_UPI}
)
UPI_LITE_MAX_PAISE = 100_000  # Rs 1,000 per-transaction UPI Lite ceiling


def stream(params: GenerationParams, name: str, merchant_id: str) -> random.Random:
    """An independent, reproducible random stream. String seeds hash via SHA-512."""
    return random.Random(f"{params.seed}/{name}/{merchant_id}")


def _bps_to_percent(bps: int) -> Decimal:
    return (Decimal(bps) / Decimal(100)).quantize(Decimal("0.01"))


def _round_bps(rng: random.Random, low: int, high: int) -> int:
    return rng.randrange(low, high + 1, 5)


@dataclass
class MerchantWorld:
    merchant: dict
    agreement: dict
    snapshots: list[dict] = field(default_factory=list)
    faults: list[FaultProfile] = field(default_factory=list)
    txn_per_day: int = 0
    amount_mu_rupees: float = 650.0
    amount_sigma: float = 1.0

    @property
    def merchant_id(self) -> str:
        return self.merchant["merchant_id"]

    @property
    def is_full(self) -> bool:
        return self.merchant["fidelity"] == "FULL"

    def agreement_rates(self) -> RateCard:
        a = self.agreement
        return RateCard(
            Decimal(a["card_credit_rate_percent"]),
            Decimal(a["card_debit_rate_percent"]),
            Decimal(a["netbanking_rate_percent"]),
        )

    def snapshot_on(self, on: date) -> dict:
        current = self.snapshots[0]
        for snap in self.snapshots:
            if date.fromisoformat(snap["effective_from"]) <= on:
                current = snap
            else:
                break
        return current

    def snapshot_rates(self, snap: dict) -> RateCard:
        return RateCard(
            Decimal(snap["card_credit_rate_percent"]),
            Decimal(snap["card_debit_rate_percent"]),
            Decimal(snap["netbanking_rate_percent"]),
        )

    def active_fault(self, fault: Fault, on: date) -> FaultProfile | None:
        for profile in self.faults:
            if profile.fault is fault and profile.active_from <= on:
                return profile
        return None


# -- merchants ----------------------------------------------------------


def build_worlds(params: GenerationParams, tariff: ProcessorTariff) -> list[MerchantWorld]:
    worlds: list[MerchantWorld] = []
    for index in range(1, params.merchants + 1):
        merchant_id = f"MER-{index:04d}"
        rng = stream(params, "merchant", merchant_id)
        is_hero = index == 1
        is_full = index <= params.full_fidelity_count

        if is_hero:
            hero = params.hero
            name, mcc, city, eco = hero.legal_name, hero.registered_mcc, hero.city, False
            acquirer = HERO_ACQUIRER
            txn_per_day, mu, sigma = params.hero_txn_per_day, 650.0, 1.0
        else:
            mcc, label, eco_capable = COHORT_MCCS[rng.randrange(len(COHORT_MCCS))]
            city = CITIES[rng.randrange(len(CITIES))]
            name = f"{city} {label} {index:04d}"
            acquirer = ACQUIRERS[rng.randrange(len(ACQUIRERS))]
            eco = eco_capable and rng.random() < 0.6
            if rng.random() < 0.2:  # micro merchant
                txn_per_day, mu, sigma = rng.randint(6, 12), 250.0, 0.8
            else:
                low, high = params.cohort_txn_per_day
                txn_per_day, mu, sigma = rng.randint(low, high), 650.0, 1.0

        # Estimated monthly bank-UPI inflow decides the NPCI class.
        upi_share = INSTRUMENT_MIX[Instrument.UPI_P2M_BANK] / sum(INSTRUMENT_MIX.values())
        est_monthly_rupees = txn_per_day * 30 * mu * math.exp(sigma**2 / 2) * upi_share
        upi_class = MerchantClass.P2PM if est_monthly_rupees <= 100_000 else MerchantClass.P2M

        onboarded = params.window_start - timedelta(days=rng.randint(30, 900))
        merchant = {
            "merchant_id": merchant_id,
            "legal_name": name,
            "registered_mcc": mcc,
            "city": city,
            "acquirer_id": acquirer,
            "fidelity": "FULL" if is_full else "SIGNATURE_ONLY",
            "onboarded_on": onboarded.isoformat(),
            "is_ecommerce_participant": eco,
            "upi_class": str(upi_class),
        }
        agreement = {
            "agreement_id": f"AGR-{merchant_id}",
            "merchant_id": merchant_id,
            "signed_on": onboarded.isoformat(),
            "settlement_sla_days": 1 if rng.random() < 0.85 else 2,
            "card_credit_rate_percent": str(_bps_to_percent(_round_bps(rng, *CARD_CREDIT_BPS))),
            "card_debit_rate_percent": str(_bps_to_percent(_round_bps(rng, *CARD_DEBIT_BPS))),
            "netbanking_rate_percent": str(_bps_to_percent(_round_bps(rng, *NETBANKING_BPS))),
        }
        world = MerchantWorld(merchant, agreement, txn_per_day=txn_per_day,
                              amount_mu_rupees=mu, amount_sigma=sigma)
        world.faults = assign_faults(params, world, tariff, is_hero=is_hero)
        world.snapshots = build_snapshots(world)
        worlds.append(world)
    return worlds


# -- faults -------------------------------------------------------------


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange(max(1, (end - start).days)))


def assign_faults(
    params: GenerationParams, world: MerchantWorld, tariff: ProcessorTariff, *, is_hero: bool
) -> list[FaultProfile]:
    """Draw every probability unconditionally so the stream never shifts."""
    rng = stream(params, "faults", world.merchant_id)
    probs = params.faults
    m = world.merchant
    mid = world.merchant_id
    start, end = params.window_start, params.as_of

    draws = {name: rng.random() for name in (
        "acq_b", "acq_c", "mcc", "drift", "small", "ppi")}
    # Random faults start in the first three quarters of the window, so a fault
    # always has enough history behind it to be observable.
    latest_start = start + (end - start) * 3 // 4
    dates = {name: _random_date(rng, start, latest_start) for name in ("drift", "small", "ppi")}
    drift_bps = rng.choice((20, 25, 30))

    faults: list[FaultProfile] = []
    if not params.leakage:
        return faults

    registered_rate, _ = tariff.rupay_cc_rate(m["registered_mcc"])
    default_rate, _ = tariff.rupay_cc_default
    mcc_misconfig_matters = registered_rate.value < default_rate.value

    if is_hero:
        hero = params.hero
        faults.append(FaultProfile(
            mid, Fault.MCC_MISCONFIG, hero.mcc_misconfig_from,
            f"Processor pricing MCC set to {hero.misconfigured_mcc} (default band) instead of "
            f"registered {hero.registered_mcc} when the RuPay-CC-on-UPI MDR table was loaded",
            {"pricing_mcc": hero.misconfigured_mcc},
        ))
        faults.append(FaultProfile(
            mid, Fault.CONTRACT_RATE_DRIFT, hero.contract_drift_from,
            f"Credit card rate on processor rate card raised {hero.contract_drift_bps} bps "
            "above the signed agreement without an amendment",
            {"drift_bps": hero.contract_drift_bps},
        ))
        faults.append(FaultProfile(
            mid, Fault.PPI_PASSTHROUGH, hero.ppi_passthrough_from,
            "Wallet-on-UPI interchange passed through to merchant as MDR; contractual basis unknown",
        ))
    else:
        if mcc_misconfig_matters and draws["mcc"] < probs.mcc_misconfig:
            faults.append(FaultProfile(
                mid, Fault.MCC_MISCONFIG, RUPAY_CC_TABLE_LOADED_ON,
                f"Processor pricing MCC defaulted instead of registered {m['registered_mcc']} "
                "when the RuPay-CC-on-UPI MDR table was loaded",
                {"pricing_mcc": "5999"},
            ))
        if draws["drift"] < probs.contract_rate_drift:
            faults.append(FaultProfile(
                mid, Fault.CONTRACT_RATE_DRIFT, dates["drift"],
                f"Credit card rate raised {drift_bps} bps above agreement without amendment",
                {"drift_bps": drift_bps},
            ))
        if draws["small"] < probs.upi_small_mdr:
            faults.append(FaultProfile(
                mid, Fault.UPI_SMALL_MDR, dates["small"],
                "A fraction of bank-account UPI transactions mis-tagged as debit card and charged debit MDR",
                {"fraction": params.upi_small_mdr_fraction},
            ))
        if draws["ppi"] < probs.ppi_passthrough:
            faults.append(FaultProfile(
                mid, Fault.PPI_PASSTHROUGH, dates["ppi"],
                "Wallet-on-UPI interchange passed through to merchant as MDR; contractual basis unknown",
            ))

    if m["acquirer_id"] == "ACQ-B" and (is_hero or draws["acq_b"] < probs.acq_b_upi_mdr_early):
        faults.append(FaultProfile(
            mid, Fault.UPI_MDR_EARLY, ACQ_B_UPI_MDR_EARLY_FROM,
            "Acquirer enabled the NPCI 0.4% UPI P2M MDR ahead of its 15 Oct 2026 effective date",
            systemic_scope="acquirer:ACQ-B",
        ))
    if m["acquirer_id"] == "ACQ-C" and draws["acq_c"] < probs.acq_c_rupay_debit_as_debit:
        faults.append(FaultProfile(
            mid, Fault.RUPAY_DEBIT_AS_DEBIT, ACQ_C_RUPAY_DEBIT_FROM,
            "Acquirer BIN table classifies RuPay debit as generic debit and applies debit MDR",
            systemic_scope="acquirer:ACQ-C",
        ))
    return sorted(faults, key=lambda f: (f.active_from, f.fault))


def build_snapshots(world: MerchantWorld) -> list[dict]:
    """Rate-card history. Config-visible faults appear here as dated changes."""
    a, m = world.agreement, world.merchant
    state = {
        "pricing_mcc": m["registered_mcc"],
        "card_credit_rate_percent": a["card_credit_rate_percent"],
        "card_debit_rate_percent": a["card_debit_rate_percent"],
        "netbanking_rate_percent": a["netbanking_rate_percent"],
    }
    snaps = [{"effective_from": m["onboarded_on"], **state}]
    for profile in world.faults:
        if profile.fault is Fault.MCC_MISCONFIG:
            state = {**state, "pricing_mcc": profile.params["pricing_mcc"]}
        elif profile.fault is Fault.CONTRACT_RATE_DRIFT:
            raised = Decimal(a["card_credit_rate_percent"]) + _bps_to_percent(profile.params["drift_bps"])
            state = {**state, "card_credit_rate_percent": str(raised)}
        else:
            continue
        snaps.append({"effective_from": profile.active_from.isoformat(), **state})
    for n, snap in enumerate(snaps, start=1):
        snap["snapshot_id"] = f"CFG-{world.merchant_id}-{n:02d}"
        snap["merchant_id"] = world.merchant_id
    return snaps


# -- ledger -------------------------------------------------------------

_HOUR_WEIGHTS = {8: 2, 9: 4, 10: 6, 11: 8, 12: 9, 13: 8, 14: 6, 15: 6, 16: 7,
                 17: 9, 18: 11, 19: 12, 20: 11, 21: 8, 22: 5, 23: 3}
_INSTRUMENTS = tuple(INSTRUMENT_MIX)
_INSTRUMENT_WEIGHTS = tuple(INSTRUMENT_MIX.values())


def _day_multiplier(day: date) -> float:
    mult = 1.2 if day.weekday() >= 5 else 1.0
    if (day.month, day.day) >= (10, 18) and (day.month, day.day) <= (11, 5):
        mult *= 1.6  # festive season
    return mult


def _amount_paise(rng: random.Random, mu: float, sigma: float) -> int:
    rupees = rng.lognormvariate(math.log(mu), sigma)
    rupees = min(max(rupees, 10.0), 150_000.0)
    if rng.random() < 0.7:
        return int(round(rupees)) * 100
    return int(round(rupees * 100))


def build_ledger(params: GenerationParams, world: MerchantWorld) -> list[dict]:
    rng = stream(params, "ledger", world.merchant_id)
    mid = world.merchant_id
    payments: list[dict] = []
    followups: list[dict] = []
    n_pay = n_ref = n_cbk = 0
    hours, hour_weights = tuple(_HOUR_WEIGHTS), tuple(_HOUR_WEIGHTS.values())

    day = params.window_start
    while day <= params.as_of:
        expected = world.txn_per_day * _day_multiplier(day)
        count = max(0, int(round(rng.gauss(expected, math.sqrt(expected)))))
        for _ in range(count):
            n_pay += 1
            hour = rng.choices(hours, hour_weights)[0]
            captured = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)), IST)
            instrument = rng.choices(_INSTRUMENTS, _INSTRUMENT_WEIGHTS)[0]
            amount = _amount_paise(rng, world.amount_mu_rupees, world.amount_sigma)
            if instrument is Instrument.UPI_LITE and amount > UPI_LITE_MAX_PAISE:
                instrument = Instrument.UPI_P2M_BANK
            failed = rng.random() < params.failure_probability
            refund_draw, refund_days, partial_draw, partial_frac = (
                rng.random(), rng.randint(0, 10), rng.random(), rng.uniform(0.2, 0.8))
            cbk_draw, cbk_days = rng.random(), rng.randint(20, 45)

            txn_id = f"TXN-{mid}-{n_pay:07d}"
            payments.append({
                "txn_id": txn_id, "merchant_id": mid, "kind": "PAYMENT",
                "status": "FAILED" if failed else "SUCCESS", "instrument": str(instrument),
                "amount_paise": amount, "captured_at": captured.isoformat(),
                "parent_txn_id": None, "order_ref": f"ORD-{n_pay:08d}",
            })
            if failed:
                continue
            if refund_draw < params.refund_probability:
                refund_at = captured + timedelta(days=refund_days, hours=2)
                if refund_at.date() <= params.as_of:
                    n_ref += 1
                    refund_amt = amount if partial_draw >= 0.3 else max(100, int(amount * partial_frac))
                    followups.append({
                        "txn_id": f"RFD-{mid}-{n_ref:06d}", "merchant_id": mid, "kind": "REFUND",
                        "status": "SUCCESS", "instrument": str(instrument),
                        "amount_paise": refund_amt, "captured_at": refund_at.isoformat(),
                        "parent_txn_id": txn_id, "order_ref": f"ORD-{n_pay:08d}",
                    })
            elif instrument in CARD_LIKE and cbk_draw < params.chargeback_probability:
                cbk_at = captured + timedelta(days=cbk_days, hours=5)
                if cbk_at.date() <= params.as_of:
                    n_cbk += 1
                    followups.append({
                        "txn_id": f"CBK-{mid}-{n_cbk:06d}", "merchant_id": mid, "kind": "CHARGEBACK",
                        "status": "SUCCESS", "instrument": str(instrument),
                        "amount_paise": amount, "captured_at": cbk_at.isoformat(),
                        "parent_txn_id": txn_id, "order_ref": f"ORD-{n_pay:08d}",
                    })
        day += timedelta(days=1)

    ledger = payments + followups
    ledger.sort(key=lambda t: (t["captured_at"], t["txn_id"]))
    return ledger
