"""Network intelligence across merchants, without sharing merchant data.

Each merchant's agent emits NetworkSignatures for its proven and escalated
cases. The Network Pattern Engine accepts nothing else -- not cases, not
ledgers, not dictionaries -- and surfaces a pattern only when at least k
distinct emitters share it. Below k, the pattern is counted as suppressed and
nothing about it is revealed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from mfp.cases.state_machine import Case
from mfp.core.enums import CaseState
from mfp.schemas.network import NetworkSignature, emitter_hash, impact_floor, merchant_segment, occurrences_bucket

EMITTABLE = frozenset({CaseState.PROVEN, CaseState.ACTION_PENDING, CaseState.BATCHED, CaseState.FILED,
                       CaseState.WAITING, CaseState.FOLLOW_UP, CaseState.REJECTED, CaseState.REPRESENT,
                       CaseState.RECOVERED, CaseState.PARTIALLY_RECOVERED, CaseState.CLOSED_UNRECOVERED,
                       CaseState.ESCALATED})


# The instrument carries no meaning for these patterns, so every emitter omits it
# and signatures from different merchants aggregate into one pattern.
INSTRUMENTLESS = frozenset({"refund_debited_twice", "refund_without_refund_event",
                            "payment_missing_from_settlement", "tax_on_non_eco_flow"})


class SignatureEmitter:
    """Runs inside a merchant's context. Turns a case into its shareable shape."""

    def __init__(self, salt: str) -> None:
        self.salt = salt

    def from_case(self, case: Case, *, mcc: str, route: str) -> NetworkSignature | None:
        if case.state not in EMITTABLE or case.proof is None:
            return None
        emitter = emitter_hash(self.salt, case.merchant_id)
        amount = case.proof.discrepancy_paise
        rule_id = case.rule_id.split(":")[0]
        return NetworkSignature(
            signature_id=f"SIG-{emitter}-{case.pattern}-{case.instrument or 'ANY'}-{case.month.replace('-', '')}",
            emitter=emitter, discrepancy_type=case.discrepancy_type, pattern=case.pattern,
            instrument=None if case.pattern in INSTRUMENTLESS else case.instrument,
            rule_id=rule_id, processor_route=route,
            merchant_segment=merchant_segment(mcc), month=case.month,
            occurrences_bucket=occurrences_bucket(len(case.txn_ids)), impact_floor_paise=impact_floor(amount),
        )


@dataclass
class SystemicPattern:
    discrepancy_type: str
    pattern: str
    instrument: str
    merchants_affected: int
    first_month: str
    last_month: str
    aggregate_impact_floor_paise: int
    top_route: str
    top_route_share: float
    segments: int
    rule_ids: list[str]

    def summary(self) -> dict[str, Any]:
        return dict(self.__dict__)


class NetworkPatternEngine:
    def __init__(self, k_anonymity: int = 5) -> None:
        self.k = k_anonymity
        self._signatures: dict[str, NetworkSignature] = {}

    def ingest(self, signatures: Iterable[NetworkSignature]) -> int:
        added = 0
        for sig in signatures:
            if not isinstance(sig, NetworkSignature):
                raise TypeError("the network accepts NetworkSignature objects only, never raw case or ledger data")
            if sig.signature_id not in self._signatures:
                added += 1
            self._signatures[sig.signature_id] = sig
        return added

    def __len__(self) -> int:
        return len(self._signatures)

    def patterns(self) -> tuple[list[SystemicPattern], int]:
        """(patterns meeting k-anonymity, count of suppressed patterns)."""
        groups: dict[tuple, list[NetworkSignature]] = defaultdict(list)
        for sig in self._signatures.values():
            groups[sig.aggregation_key].append(sig)
        found: list[SystemicPattern] = []
        suppressed = 0
        for (dtype, pattern, instrument), sigs in groups.items():
            emitters = {s.emitter for s in sigs}
            if len(emitters) < self.k:
                suppressed += 1
                continue
            routes = Counter()
            for emitter in emitters:
                route = Counter(s.processor_route for s in sigs if s.emitter == emitter).most_common(1)[0][0]
                routes[route] += 1
            top_route, top_count = routes.most_common(1)[0]
            months = sorted(s.month for s in sigs)
            found.append(SystemicPattern(
                discrepancy_type=dtype, pattern=pattern, instrument=instrument, merchants_affected=len(emitters),
                first_month=months[0], last_month=months[-1],
                aggregate_impact_floor_paise=sum(s.impact_floor_paise for s in sigs),
                top_route=top_route, top_route_share=round(top_count / len(emitters), 3),
                segments=len({s.merchant_segment for s in sigs}), rule_ids=sorted({s.rule_id for s in sigs}),
            ))
        found.sort(key=lambda p: (-p.merchants_affected, -p.aggregate_impact_floor_paise))
        return found, suppressed
