"""The rule schema.

Financial rates never live in code. They live in config/*.json and are loaded
through this schema, which forces every rule to declare where it came from,
how confident we are, and what we had to assume.

Two design decisions carry the safety of the whole system:

1. Rules are TEMPORAL. Every rule has effective_from/effective_to, because our
   12-month window spans three distinct UPI pricing regimes (pre 1 Jun 2026,
   1 Jun - 14 Oct 2026, and 15 Oct 2026 onward).

2. Rule resolution must be UNAMBIGUOUS. If two rules of equal precedence match
   the same query, or none match, the Rule Engine raises rather than guessing.
   That exception is what routes a case to ESCALATE instead of to a claim.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mfp.core.enums import (
    CalculationKind,
    Confidence,
    Instrument,
    MerchantClass,
    RoundingPolicy,
    RuleType,
    VerificationStatus,
)


class RuleResolutionError(RuntimeError):
    """Base for every failure to resolve exactly one rule."""


class AmbiguousRuleError(RuleResolutionError):
    """Two or more rules of equal precedence matched. Never guess -- escalate."""


class NoApplicableRuleError(RuleResolutionError):
    """No rule matched the query. Never assume zero -- escalate."""


class RuleSource(BaseModel):
    """Provenance. A rule without a source cannot be loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: str = Field(min_length=1)  # e.g. "NPCI", "CBIC", "MoF/DFS", "Canara Bank"
    document: str = Field(min_length=1)
    url: str | None = None
    published_on: date | None = None
    retrieved_on: date


class AmountRange(BaseModel):
    """Inclusive-exclusive paise band: min_paise <= amount < max_paise."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_paise: int = 0
    max_paise: int | None = None  # None means unbounded

    @model_validator(mode="after")
    def _ordered(self) -> AmountRange:
        if self.max_paise is not None and self.max_paise <= self.min_paise:
            raise ValueError("max_paise must exceed min_paise")
        return self

    def contains(self, amount_paise: int) -> bool:
        if amount_paise < self.min_paise:
            return False
        return self.max_paise is None or amount_paise < self.max_paise


class RuleScope(BaseModel):
    """What a rule applies to. An empty list means 'any'."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instruments: tuple[Instrument, ...] = ()
    merchant_classes: tuple[MerchantClass, ...] = ()
    mccs: tuple[str, ...] = ()
    acquirers: tuple[str, ...] = ()
    amount_range: AmountRange = AmountRange()
    is_ecommerce_participant: bool | None = None  # None means 'either'; gates TCS/TDS
    turnover_range: AmountRange | None = None      # previous-year turnover band, in paise


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    rule_type: RuleType
    calculation: CalculationKind

    # Interpretation depends on `calculation`:
    #   NIL              -> value must be absent
    #   FLAT_PERCENT     -> value is a percentage, e.g. "0.4"
    #   PERCENT_WITH_CAP -> value is a percentage, cap_paise is required
    #   FLAT_FEE         -> value_paise is required
    value_percent: Decimal | None = None
    value_paise: int | None = None
    cap_paise: int | None = None

    scope: RuleScope = RuleScope()
    effective_from: date
    effective_to: date | None = None  # None means still in force
    precedence: int = 100             # higher wins; equal + both matching = ambiguous

    source: RuleSource
    confidence: Confidence
    verification_status: VerificationStatus
    assumptions: tuple[str, ...] = ()
    notes: str | None = None

    @field_validator("value_percent", mode="before")
    @classmethod
    def _no_float_percent(cls, v):
        if isinstance(v, float):
            raise ValueError("value_percent must be a string or Decimal, never float")
        return v

    @model_validator(mode="after")
    def _calculation_shape(self) -> Rule:
        kind = self.calculation
        if kind is CalculationKind.NIL:
            if self.value_percent is not None or self.value_paise is not None:
                raise ValueError(f"{self.rule_id}: NIL rules carry no value")
        elif kind is CalculationKind.FLAT_PERCENT:
            if self.value_percent is None:
                raise ValueError(f"{self.rule_id}: FLAT_PERCENT needs value_percent")
        elif kind is CalculationKind.PERCENT_WITH_CAP:
            if self.value_percent is None or self.cap_paise is None:
                raise ValueError(
                    f"{self.rule_id}: PERCENT_WITH_CAP needs value_percent and cap_paise"
                )
        elif kind is CalculationKind.FLAT_FEE:
            if self.value_paise is None:
                raise ValueError(f"{self.rule_id}: FLAT_FEE needs value_paise")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError(f"{self.rule_id}: effective_to precedes effective_from")
        return self

    @model_validator(mode="after")
    def _assumed_rules_declare_why(self) -> Rule:
        if self.verification_status is VerificationStatus.ASSUMED and not self.assumptions:
            raise ValueError(
                f"{self.rule_id}: a rule marked ASSUMED must list its assumptions. "
                "Unverified rates may not enter the system silently."
            )
        return self

    # -- matching --------------------------------------------------------
    def in_force_on(self, as_of: date) -> bool:
        if as_of < self.effective_from:
            return False
        return self.effective_to is None or as_of <= self.effective_to

    @property
    def can_support_auto_claim(self) -> bool:
        """ASSUMED rules may inform an investigation but never file a claim."""
        return self.verification_status is not VerificationStatus.ASSUMED


class RoundingRule(BaseModel):
    """Rounding is a financial rule, not a constant. See core/money.apply_rate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    policy: RoundingPolicy
    applies_at: str  # "per_transaction" | "per_batch"
    source: RuleSource
    confidence: Confidence
    verification_status: VerificationStatus
    assumptions: tuple[str, ...] = ()


class MaterialityRule(BaseModel):
    """Two-tier threshold.

    per_case_floor_paise suppresses single-transaction noise; a case below it is
    held as BATCHED rather than discarded, so systematic sub-threshold leakage
    still surfaces once aggregate_floor_paise is crossed across a signature.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    per_case_floor_paise: int
    aggregate_floor_paise: int
    notes: str | None = None


class RuleSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    rule_set_id: str
    description: str | None = None
    rules: tuple[Rule, ...] = ()
    rounding_rules: tuple[RoundingRule, ...] = ()
    materiality_rules: tuple[MaterialityRule, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> RuleSet:
        seen: set[str] = set()
        for rule in self.rules:
            if rule.rule_id in seen:
                raise ValueError(f"duplicate rule_id: {rule.rule_id}")
            seen.add(rule.rule_id)
        return self
