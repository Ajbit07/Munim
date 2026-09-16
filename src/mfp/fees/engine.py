"""The Fee Engine.

Computes what a payment SHOULD have been charged, from the Rule Engine and the
merchant's signed agreement, and decomposes what it WAS charged into
attributable components:

  MDR component  -- MDR above expected, plus standard GST on that excess   (L1, L2)
  GST component  -- GST above the correct GST on the MDR actually charged (L3)
  TAX component  -- TCS and TDS above what applies                         (L4)

Rounding. The rounding convention is an ASSUMED rule. So every amount is
computed under all three policies. A discrepancy that exists under some
policies but not others depends on the assumption and cannot be proven; one
that exists under all of them does not depend on it at all. The claimable
amount is the minimum across policies.

This module is an implementation independent of the generator's processor.
It shares config (the published rules) and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from mfp.core.enums import Instrument, MerchantClass, RoundingPolicy, RuleType, CalculationKind
from mfp.core.money import Money, Rate, apply_rate
from mfp.rules.engine import RuleEngine, RuleQuery
from mfp.schemas.ledger import Merchant, MerchantAgreement
from mfp.schemas.rules import Rule, RuleResolutionError

POLICIES: tuple[RoundingPolicy, ...] = (RoundingPolicy.HALF_UP, RoundingPolicy.HALF_EVEN, RoundingPolicy.TRUNCATE)
CONTRACTUAL = frozenset({Instrument.CARD_CREDIT, Instrument.CARD_DEBIT, Instrument.NETBANKING})
PROTECTED_LOOKING = frozenset({Instrument.UPI_P2M_BANK, Instrument.UPI_LITE, Instrument.RUPAY_DEBIT, Instrument.PPI_ON_UPI})

_MDR_TYPES = frozenset({RuleType.MDR, RuleType.NIL_PROTECTION})
_GST_TYPES = frozenset({RuleType.GST, RuleType.GST_EXEMPTION})


class ByPolicy(dict):
    """{RoundingPolicy: paise}. Arithmetic is element-wise."""

    @classmethod
    def const(cls, value: int) -> ByPolicy:
        return cls({p: value for p in POLICIES})

    def __add__(self, other):  # type: ignore[override]
        o = other if isinstance(other, dict) else ByPolicy.const(other)
        return ByPolicy({p: self[p] + o[p] for p in POLICIES})

    def __sub__(self, other):
        o = other if isinstance(other, dict) else ByPolicy.const(other)
        return ByPolicy({p: self[p] - o[p] for p in POLICIES})

    @property
    def low(self) -> int:
        return min(self.values())

    @property
    def high(self) -> int:
        return max(self.values())


def _pct(amount: int, rate: Rate) -> ByPolicy:
    return ByPolicy({p: apply_rate(Money(amount), rate, p).paise for p in POLICIES})


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    rule_type: str
    auto_claimable: bool
    description: str


@dataclass
class Expected:
    mdr: ByPolicy
    gst: ByPolicy
    tcs: ByPolicy
    tds: ByPolicy
    mdr_rule: RuleRef
    gst_rule: RuleRef | None
    tax_rules: tuple[RuleRef, ...]
    gst_rate: Rate | None
    gst_exempt: bool

    @property
    def total(self) -> ByPolicy:
        return self.mdr + self.gst + self.tcs + self.tds

    @property
    def assumed_rules(self) -> tuple[str, ...]:
        refs = [self.mdr_rule, self.gst_rule, *self.tax_rules]
        return tuple(r.rule_id for r in refs if r is not None and not r.auto_claimable)


@dataclass
class Unresolved:
    reason: str
    stage: str


@dataclass
class Component:
    component: str                 # MDR | GST | TAX
    amount: ByPolicy               # positive = merchant overcharged
    actual_paise: int
    expected: ByPolicy
    rule: RuleRef
    pattern: str
    discrepancy_type: str
    notes: list[str] = field(default_factory=list)

    @property
    def assumed(self) -> bool:
        return not self.rule.auto_claimable


def _ref(rule: Rule) -> RuleRef:
    return RuleRef(rule.rule_id, str(rule.rule_type), rule.can_support_auto_claim, rule.name)


class FeeEngine:
    def __init__(self, rules: RuleEngine) -> None:
        self.rules = rules
        self.configured_policy = rules.rounding.policy

    # -- rules ---------------------------------------------------------------

    def _query(self, types, instrument, amount, on, merchant: Merchant) -> Rule:
        return self.rules.resolve(RuleQuery(
            rule_types=types, on=on, instrument=instrument, amount_paise=amount,
            mcc=merchant.registered_mcc, merchant_class=MerchantClass(merchant.upi_class),
            acquirer=merchant.acquirer_id, is_ecommerce_participant=merchant.is_ecommerce_participant,
        ))

    @staticmethod
    def agreement_on(agreements: list[MerchantAgreement], on: date) -> MerchantAgreement | None:
        valid = [a for a in agreements if a.signed_on <= on]
        return max(valid, key=lambda a: a.signed_on) if valid else None

    def _apply_rule(self, rule: Rule, base: int) -> ByPolicy:
        kind = rule.calculation
        if kind is CalculationKind.NIL:
            return ByPolicy.const(0)
        if kind is CalculationKind.FLAT_FEE:
            return ByPolicy.const(rule.value_paise or 0)
        amounts = _pct(base, Rate.from_percent(rule.value_percent))
        if kind is CalculationKind.PERCENT_WITH_CAP:
            return ByPolicy({p: min(v, rule.cap_paise) for p, v in amounts.items()})
        return amounts

    # -- expected ------------------------------------------------------------

    def expected_mdr(self, instrument: Instrument, amount: int, on: date, merchant: Merchant,
                     agreements: list[MerchantAgreement]) -> tuple[ByPolicy, RuleRef] | Unresolved:
        if instrument in CONTRACTUAL:
            agreement = self.agreement_on(agreements, on)
            if agreement is None:
                return Unresolved(f"no signed agreement in force on {on}", "MDR")
            percent = {
                Instrument.CARD_CREDIT: agreement.card_credit_rate_percent,
                Instrument.CARD_DEBIT: agreement.card_debit_rate_percent,
                Instrument.NETBANKING: agreement.netbanking_rate_percent,
            }[instrument]
            ref = RuleRef(f"AGREEMENT:{agreement.agreement_id}", "AGREEMENT", True,
                          f"{instrument} at {percent}% per agreement signed {agreement.signed_on}")
            return _pct(amount, Rate.from_percent(percent)), ref
        try:
            rule = self._query(_MDR_TYPES, instrument, amount, on, merchant)
        except RuleResolutionError as exc:
            return Unresolved(str(exc), "MDR")
        return self._apply_rule(rule, amount), _ref(rule)

    def gst_rule(self, instrument: Instrument, amount: int, on: date, merchant: Merchant) -> Rule | Unresolved:
        try:
            return self._query(_GST_TYPES, instrument, amount, on, merchant)
        except RuleResolutionError as exc:
            return Unresolved(str(exc), "GST")

    def gst_on(self, rule: Rule, mdr: ByPolicy | int) -> ByPolicy:
        mdr_bp = mdr if isinstance(mdr, dict) else ByPolicy.const(mdr)
        if rule.calculation is CalculationKind.NIL:
            return ByPolicy.const(0)
        rate = Rate.from_percent(rule.value_percent)
        return ByPolicy({p: apply_rate(Money(mdr_bp[p]), rate, p).paise for p in POLICIES})

    def expected_taxes(self, amount: int, on: date, merchant: Merchant
                       ) -> tuple[dict[RuleType, ByPolicy], list[RuleRef]] | Unresolved:
        taxes: dict[RuleType, ByPolicy] = {}
        refs: list[RuleRef] = []
        for rtype in (RuleType.TCS, RuleType.TDS):
            try:
                rule = self._query(frozenset({rtype}), None, None, on, merchant)
            except RuleResolutionError as exc:
                return Unresolved(str(exc), str(rtype))
            taxes[rtype] = self._apply_rule(rule, amount)
            refs.append(_ref(rule))
        return taxes, refs

    def expected(self, instrument: Instrument, amount: int, on: date, merchant: Merchant,
                 agreements: list[MerchantAgreement]) -> Expected | Unresolved:
        mdr = self.expected_mdr(instrument, amount, on, merchant, agreements)
        if isinstance(mdr, Unresolved):
            return mdr
        mdr_amount, mdr_ref = mdr
        gst_rule = self.gst_rule(instrument, amount, on, merchant)
        if isinstance(gst_rule, Unresolved):
            return gst_rule
        gst = self.gst_on(gst_rule, mdr_amount)
        taxes_or_error = self.expected_taxes(amount, on, merchant)
        if isinstance(taxes_or_error, Unresolved):
            return taxes_or_error
        taxes, tax_rules = taxes_or_error
        return Expected(
            mdr=mdr_amount, gst=gst, tcs=taxes[RuleType.TCS], tds=taxes[RuleType.TDS],
            mdr_rule=mdr_ref, gst_rule=_ref(gst_rule), tax_rules=tuple(tax_rules),
            gst_rate=None if gst_rule.calculation is CalculationKind.NIL else Rate.from_percent(gst_rule.value_percent),
            gst_exempt=gst_rule.rule_type is RuleType.GST_EXEMPTION,
        )

    # -- decomposition -------------------------------------------------------

    def decompose(self, instrument: Instrument, amount: int, on: date, merchant: Merchant,
                  agreements: list[MerchantAgreement], *, mdr: int, gst: int, tcs: int, tds: int
                  ) -> list[Component] | Unresolved:
        """Attribute actual deductions to MDR, GST and TAX components.

        Returns only components where the merchant was charged MORE than
        expected under the configured policy. Undercharges are not claimable.
        """
        exp = self.expected(instrument, amount, on, merchant, agreements)
        if isinstance(exp, Unresolved):
            if exp.stage in ("MDR", "GST") and mdr + gst == 0:
                # Nothing was charged under the rule we cannot resolve, so there
                # is nothing to dispute there. Taxes are still checked on their own.
                taxes = self.expected_taxes(amount, on, merchant)
                if isinstance(taxes, Unresolved):
                    return taxes if tcs + tds > 0 else []
                rates, refs = taxes
                tax_amount = ByPolicy.const(tcs + tds) - (rates[RuleType.TCS] + rates[RuleType.TDS])
                if tax_amount[self.configured_policy] > 0:
                    return [Component("TAX", tax_amount, tcs + tds, rates[RuleType.TCS] + rates[RuleType.TDS],
                                      refs[0], "tax_on_non_eco_flow", "L4_TAX_MISAPPLICATION")]
                return []
            return exp if mdr + gst + tcs + tds > 0 else []

        found: list[Component] = []
        cfg = self.configured_policy
        gst_rule = self.rules.rule(exp.gst_rule.rule_id) if exp.gst_rule else None
        gst_on_actual = self.gst_on(gst_rule, mdr) if gst_rule else ByPolicy.const(0)

        gst_excess = ByPolicy.const(gst) - gst_on_actual
        # A deduction that rests on an ASSUMED rule cannot be judged either way.
        if not exp.mdr_rule.auto_claimable and mdr > 0:
            disputed = ByPolicy.const(mdr) + (gst_on_actual if gst_excess[cfg] > 0 else ByPolicy.const(gst))
            found.append(Component(
                "MDR", disputed, mdr + gst, exp.mdr + exp.gst, exp.mdr_rule,
                "unverified_interchange_passthrough", "L2_NIL_MDR_VIOLATION",
                [f"governing rule {exp.mdr_rule.rule_id} is ASSUMED; charge cannot be verified"],
            ))
            if gst_excess[cfg] > 0 and exp.gst_rule is not None:
                found.append(Component("GST", gst_excess, gst, gst_on_actual, exp.gst_rule,
                                       "gst_above_standard_base", "L3_GST_BASE_ERROR"))
        else:
            if gst_excess[cfg] > 0:
                mdr_amount = (ByPolicy.const(mdr) + gst_on_actual) - (exp.mdr + exp.gst)
            else:
                mdr_amount = ByPolicy.const(mdr + gst) - (exp.mdr + exp.gst)
            if mdr_amount[cfg] > 0:
                if exp.mdr_rule.rule_type == str(RuleType.NIL_PROTECTION):
                    pattern, dtype = "mdr_on_protected_instrument", "L2_NIL_MDR_VIOLATION"
                elif instrument in CONTRACTUAL:
                    pattern, dtype = "mdr_above_agreement", "L1_WRONG_MDR_BAND"
                else:
                    pattern, dtype = "mdr_above_mcc_rate", "L1_WRONG_MDR_BAND"
                found.append(Component("MDR", mdr_amount, mdr + min(gst, gst_on_actual[cfg]),
                                       exp.mdr + exp.gst, exp.mdr_rule, pattern, dtype))
            if gst_excess[cfg] > 0 and exp.gst_rule is not None:
                pattern = "gst_on_exempt_settlement" if exp.gst_exempt else "gst_above_standard_base"
                found.append(Component("GST", gst_excess, gst, gst_on_actual, exp.gst_rule,
                                       pattern, "L3_GST_BASE_ERROR"))

        tax_amount = ByPolicy.const(tcs + tds) - (exp.tcs + exp.tds)
        if tax_amount[cfg] > 0:
            rule = exp.tax_rules[0]
            found.append(Component("TAX", tax_amount, tcs + tds, exp.tcs + exp.tds, rule,
                                   "tax_on_non_eco_flow", "L4_TAX_MISAPPLICATION"))
        return found

    def expected_net(self, instrument: Instrument, amount: int, on: date, merchant: Merchant,
                     agreements: list[MerchantAgreement]) -> ByPolicy | Unresolved:
        exp = self.expected(instrument, amount, on, merchant, agreements)
        if isinstance(exp, Unresolved):
            return exp
        return ByPolicy.const(amount) - exp.total


def describe_rate(rule: Rule) -> str:
    if rule.calculation is CalculationKind.NIL:
        return "nil"
    if rule.calculation is CalculationKind.FLAT_FEE:
        return f"flat Rs {Decimal(rule.value_paise or 0) / 100}"
    cap = f", cap Rs {Decimal(rule.cap_paise) / 100}" if rule.cap_paise else ""
    return f"{rule.value_percent}%{cap}"
