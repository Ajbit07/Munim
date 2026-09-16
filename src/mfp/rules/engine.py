"""The Rule Engine.

Given a query, return exactly one rule, or raise.

  - No rule matches              -> NoApplicableRuleError
  - Two top-precedence rules tie -> AmbiguousRuleError

Both exceptions are routed by callers to ESCALATE. The engine never picks
arbitrarily between rules of equal standing and never assumes a zero rate.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache

from mfp.core.enums import Instrument, MerchantClass, RuleType
from mfp.rules.loader import load_fee_rules
from mfp.schemas.rules import (
    AmbiguousRuleError,
    MaterialityRule,
    NoApplicableRuleError,
    RoundingRule,
    Rule,
    RuleSet,
)


@dataclass(frozen=True, slots=True)
class RuleQuery:
    rule_types: frozenset[RuleType]
    on: date
    instrument: Instrument | None = None
    amount_paise: int | None = None
    mcc: str | None = None
    merchant_class: MerchantClass | None = None
    acquirer: str | None = None
    is_ecommerce_participant: bool | None = None

    def describe(self) -> str:
        parts = [f"types={sorted(str(t) for t in self.rule_types)}", f"on={self.on}"]
        for name in ("instrument", "amount_paise", "mcc", "merchant_class", "is_ecommerce_participant"):
            value = getattr(self, name)
            if value is not None:
                parts.append(f"{name}={value}")
        return ", ".join(parts)


class RuleEngine:
    def __init__(self, rule_set: RuleSet) -> None:
        self.rule_set = rule_set
        self.by_id: dict[str, Rule] = {r.rule_id: r for r in rule_set.rules}
        bounds = {0}
        for rule in rule_set.rules:
            bounds.add(rule.scope.amount_range.min_paise)
            if rule.scope.amount_range.max_paise is not None:
                bounds.add(rule.scope.amount_range.max_paise)
        self._amount_bounds = sorted(bounds)
        self._resolve_cached = lru_cache(maxsize=65_536)(self._resolve_banded)

    @classmethod
    def from_config(cls, config_dir=None) -> RuleEngine:
        return cls(load_fee_rules(config_dir))

    # -- matching ------------------------------------------------------------

    @staticmethod
    def matches(rule: Rule, q: RuleQuery) -> bool:
        if rule.rule_type not in q.rule_types or not rule.in_force_on(q.on):
            return False
        scope = rule.scope
        if scope.instruments and q.instrument not in scope.instruments:
            return False
        if scope.merchant_classes and q.merchant_class not in scope.merchant_classes:
            return False
        if scope.mccs and q.mcc not in scope.mccs:
            return False
        if scope.acquirers and q.acquirer not in scope.acquirers:
            return False
        if scope.is_ecommerce_participant is not None and q.is_ecommerce_participant != scope.is_ecommerce_participant:
            return False
        if q.amount_paise is not None and not scope.amount_range.contains(q.amount_paise):
            return False
        if q.amount_paise is None and (scope.amount_range.min_paise > 0 or scope.amount_range.max_paise is not None):
            return False
        return True

    def candidates(self, q: RuleQuery) -> list[Rule]:
        return [r for r in self.rule_set.rules if self.matches(r, q)]

    def resolve(self, q: RuleQuery) -> Rule:
        """Exactly one rule, or an exception. Cached by amount band, not amount."""
        band_amount = None
        if q.amount_paise is not None:
            index = bisect.bisect_right(self._amount_bounds, q.amount_paise) - 1
            band_amount = self._amount_bounds[max(index, 0)]
        # Every amount inside a band matches exactly the same rules, because the
        # bands are cut at every rule boundary. So the band floor is a safe key.
        try:
            return self._resolve_cached(replace(q, amount_paise=band_amount))
        except NoApplicableRuleError as exc:
            raise NoApplicableRuleError(f"{exc}: {q.describe()}") from None
        except AmbiguousRuleError as exc:
            raise AmbiguousRuleError(f"{exc} for {q.describe()}") from None

    def _resolve_banded(self, q: RuleQuery) -> Rule:
        found = self.candidates(q)
        if not found:
            raise NoApplicableRuleError("no rule applies")
        top = max(r.precedence for r in found)
        winners = [r for r in found if r.precedence == top]
        if len(winners) > 1:
            raise AmbiguousRuleError(
                f"{len(winners)} rules tie at precedence {top}: "
                f"{', '.join(r.rule_id for r in winners)}"
            )
        return winners[0]

    # -- non-fee rules ---------------------------------------------------------

    @property
    def rounding(self) -> RoundingRule:
        return self.rule_set.rounding_rules[0]

    @property
    def materiality(self) -> MaterialityRule:
        return self.rule_set.materiality_rules[0]

    def rule(self, rule_id: str) -> Rule:
        return self.by_id[rule_id]
