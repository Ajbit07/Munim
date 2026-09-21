"""Cases and their state machine.

Every case moves only through `CaseStateMachine.transition`, which rejects
illegal moves and records who moved it, why, on what evidence, with which
tool, and what happened -- both on the case and on the event spine. The UI
activity feed, the case drill-down and the audit trail are all this record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from mfp.core.clock import Clock
from mfp.core.enums import Actor, CaseState, Instrument
from mfp.core.events import EventLog
from mfp.schemas.proof import Candidate, ProofResult

S = CaseState

ALLOWED: dict[CaseState, frozenset[CaseState]] = {
    S.DISCOVERED: frozenset({S.INVESTIGATING}),
    S.INVESTIGATING: frozenset({S.CANDIDATE, S.NO_DISCREPANCY, S.AWAITING_CYCLE, S.ESCALATED}),
    S.AWAITING_CYCLE: frozenset({S.INVESTIGATING}),
    S.CANDIDATE: frozenset({S.PROVING}),
    S.PROVING: frozenset({S.PROVEN, S.UNPROVEN, S.NO_DISCREPANCY}),
    S.UNPROVEN: frozenset({S.ESCALATED}),
    S.NO_DISCREPANCY: frozenset({S.CLOSED}),
    S.PROVEN: frozenset({S.ACTION_PENDING, S.BATCHED}),
    S.BATCHED: frozenset({S.ACTION_PENDING}),
    S.ACTION_PENDING: frozenset({S.FILED, S.WITHDRAWN}),
    S.FILED: frozenset({S.WAITING}),
    # An approval is not money. Recovered is reachable only through AWAITING_CREDIT, once the
    # reversal credit has been seen in a settlement and the bank statement.
    S.WAITING: frozenset({S.FOLLOW_UP, S.AWAITING_CREDIT, S.REJECTED, S.WITHDRAWN}),
    S.AWAITING_CREDIT: frozenset({S.RECOVERED, S.PARTIALLY_RECOVERED, S.ESCALATED}),
    S.FOLLOW_UP: frozenset({S.WAITING, S.ESCALATED, S.WITHDRAWN}),
    S.REJECTED: frozenset({S.REPRESENT, S.CLOSED_UNRECOVERED, S.ESCALATED}),
    S.REPRESENT: frozenset({S.FILED, S.FOLLOW_UP}),
    S.PARTIALLY_RECOVERED: frozenset({S.CLOSED}),
    S.ESCALATED: frozenset({S.INVESTIGATING, S.CLOSED, S.ACTION_PENDING}),
    S.WITHDRAWN: frozenset(),
    S.RECOVERED: frozenset(),
    S.CLOSED_UNRECOVERED: frozenset(),
    S.CLOSED: frozenset(),
}

TERMINAL = frozenset(s for s, nxt in ALLOWED.items() if not nxt)

# Only a person may move a case out of the human queue. The agents cannot
# overrule the proof gate; a reviewer can, and the log says so.
HUMAN_ONLY = frozenset({(S.ESCALATED, S.ACTION_PENDING), (S.ESCALATED, S.CLOSED)})
IN_FLIGHT = frozenset({S.FILED, S.WAITING, S.FOLLOW_UP, S.REJECTED, S.REPRESENT, S.AWAITING_CREDIT})


class IllegalTransition(RuntimeError):
    pass


@dataclass
class Transition:
    at: str
    actor: str
    from_state: str
    to_state: str
    reason: str
    action: str | None = None
    tool: str | None = None
    evidence: list[str] = field(default_factory=list)
    result: str | None = None


@dataclass
class Case:
    case_id: str
    merchant_id: str
    discrepancy_type: str
    pattern: str
    component: str
    instrument: Instrument | None
    rule_id: str
    month: str
    txn_ids: tuple[str, ...]
    opened_at: str
    detected_paise: int                     # detection estimate; informational only
    state: CaseState = S.DISCOVERED
    assumed: bool = False
    unresolved_reason: str | None = None
    rationale: str | None = None
    reasoner: str | None = None
    similar_cases: list[dict] = field(default_factory=list)
    candidate: Candidate | None = None
    proof: ProofResult | None = None
    claim_id: str | None = None
    recovered_paise: int = 0
    escalation: dict[str, Any] | None = None
    root_cause_id: str | None = None
    next_check_at: datetime | None = None
    follow_ups: int = 0
    hold_reason: str | None = None
    human_attestation: dict[str, Any] | None = None
    approved_paise: int = 0                 # what settlement ops approved; not money until it is credited
    approved_at: datetime | None = None
    payout_chases: int = 0
    refund_credit: dict[str, Any] | None = None   # the settlement line and bank credit that paid it back
    regression: bool = False
    history: list[Transition] = field(default_factory=list)

    @property
    def proven_paise(self) -> int:
        return self.proof.discrepancy_paise if self.proof and self.proof.authorises_claim else 0

    @property
    def claimed_paise(self) -> int:
        """Proven by the engine, or attested by a person for a human-filed claim."""
        if self.proven_paise:
            return self.proven_paise
        if self.human_attestation and self.proof is not None:
            return self.proof.discrepancy_paise
        return 0

    @property
    def captured_range(self) -> str:
        return self.month

    def summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "merchant_id": self.merchant_id, "state": str(self.state),
            "discrepancy_type": self.discrepancy_type, "pattern": self.pattern, "component": self.component,
            "instrument": str(self.instrument) if self.instrument else None, "rule_id": self.rule_id,
            "month": self.month, "transactions": len(self.txn_ids), "detected_paise": self.detected_paise,
            "proven_paise": self.proven_paise,
            "disputed_paise": self.proof.discrepancy_paise if self.proof else self.detected_paise,
            "recovered_paise": self.recovered_paise, "verdict": str(self.proof.verdict) if self.proof else None,
            "claim_id": self.claim_id, "root_cause_id": self.root_cause_id, "follow_ups": self.follow_ups,
            "hold_reason": self.hold_reason, "escalation": self.escalation,
            "human_attestation": self.human_attestation, "regression": self.regression,
            "approved_paise": self.approved_paise, "refund_credit": self.refund_credit,
        }


class CaseStateMachine:
    def __init__(self, events: EventLog, clock: Clock) -> None:
        self.events = events
        self.clock = clock

    def transition(self, case: Case, to: CaseState, actor: Actor, reason: str, *, action: str | None = None,
                   tool: str | None = None, evidence: list[str] | None = None, result: str | None = None,
                   **payload: Any) -> Case:
        if to not in ALLOWED[case.state]:
            raise IllegalTransition(f"{case.case_id}: {case.state} -> {to} is not permitted")
        if (case.state, to) in HUMAN_ONLY and actor is not Actor.HUMAN:
            raise IllegalTransition(f"{case.case_id}: {case.state} -> {to} requires a human reviewer")
        record = Transition(self.clock.now().isoformat(), str(actor), str(case.state), str(to), reason,
                            action, tool, list(evidence or []), result)
        self.events.append(
            actor, "case.state.changed", case_id=case.case_id, merchant_id=case.merchant_id,
            from_state=record.from_state, to_state=record.to_state, reason=reason, action=action,
            tool=tool, evidence=record.evidence[:10], result=result, next_state=str(to), **payload,
        )
        case.state = to
        case.history.append(record)
        return case


class CaseRepository:
    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._by_key: dict[tuple, str] = {}
        self._seq = 0

    def next_id(self) -> str:
        self._seq += 1
        return f"CASE-{self._seq:05d}"

    def add(self, case: Case, key: tuple) -> Case:
        self._cases[case.case_id] = case
        self._by_key[key] = case.case_id
        return case

    def by_key(self, key: tuple) -> Case | None:
        cid = self._by_key.get(key)
        return self._cases.get(cid) if cid else None

    def get(self, case_id: str) -> Case:
        return self._cases[case_id]

    def all(self, merchant_id: str | None = None) -> list[Case]:
        return [c for c in self._cases.values() if merchant_id is None or c.merchant_id == merchant_id]

    def in_state(self, *states: CaseState, merchant_id: str | None = None) -> list[Case]:
        return [c for c in self.all(merchant_id) if c.state in states]

    def covered(self) -> set[tuple[str, str]]:
        """(txn_id, component) pairs already owned by a case."""
        return {(t, c.component) for c in self._cases.values() for t in c.txn_ids}
