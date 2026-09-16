"""Agent 3 -- the Follow-up Agent.

Answers: what needs to happen next, and can I complete it without a human?

It owns a case from proof to resolution:

  - files the claim, but only through the proof gate: a case must be
    ACTION_PENDING and carry a PROVEN ProofResult computed from exactly the
    candidate on the case. Anything else raises ProofGateViolation.
  - pilots before it batches: when no outcome is known yet for a pattern, it
    files one claim and holds the rest, so a predictable rejection lands once
    rather than eight times
  - learns from the desk: evidence that overturned a rejection is attached
    up front to every later claim of the same pattern
  - chases silence, represents rejections it can answer, and escalates or
    closes the ones it cannot
  - confirms the recovery and requests the configuration correction that
    stops recurrence
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from mfp.cases.state_machine import IN_FLIGHT, Case
from mfp.core.enums import Actor, CaseState
from mfp.workflow.claims import BASE_ATTACHMENTS, REMEDIES, Claim, ClaimStatus

if TYPE_CHECKING:
    from mfp.runtime.system import Runtime

ACTOR = Actor.FOLLOWUP_AGENT
S = CaseState
CHECK_AFTER = timedelta(days=3)
MAX_FOLLOW_UPS = 3
MAX_REPRESENTS = 2


class ProofGateViolation(PermissionError):
    """An attempt to file a claim that the Proof Engine has not authorised."""


class FollowUpAgent:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.claims: dict[str, Claim] = {}
        self._seq = 0

    # -- the gate ---------------------------------------------------------------

    def _assert_gate(self, case: Case) -> None:
        problems = []
        if case.state is not S.ACTION_PENDING:
            problems.append(f"case is {case.state}, not ACTION_PENDING")
        if case.proof is None or not case.proof.authorises_claim:
            problems.append("no PROVEN proof authorises this claim")
        if case.proof is not None and case.candidate is not None and case.proof.input_hash != case.candidate.input_hash():
            problems.append("the proof was computed from a different candidate")
        if problems:
            self.rt.events.append(ACTOR, "proof_gate.blocked", case_id=case.case_id, merchant_id=case.merchant_id,
                                  problems=problems, decision="refuse to file")
            raise ProofGateViolation(f"{case.case_id}: " + "; ".join(problems))

    # -- filing -----------------------------------------------------------------

    def file(self, case: Case) -> Claim | None:
        rt = self.rt
        self._assert_gate(case)

        outcomes = rt.memory.pattern_outcomes(case.pattern)
        succeeded = outcomes.get("APPROVED", 0) + outcomes.get("PARTIALLY_APPROVED", 0)
        pilot = next((c for c in rt.cases.in_state(*IN_FLIGHT) if c.pattern == case.pattern
                      and c.claim_id and c.case_id != case.case_id), None)
        if not succeeded and pilot is not None:
            reason = f"Awaiting the outcome of pilot claim {pilot.claim_id} for this pattern before filing"
            if case.hold_reason != reason:
                case.hold_reason = reason
                rt.events.append(ACTOR, "claim.held", case_id=case.case_id, merchant_id=case.merchant_id,
                                 pilot_case=pilot.case_id, reason=reason, decision="hold")
            return None
        case.hold_reason = None

        learned = rt.memory.winning_attachments(case.pattern)
        attachments = set(BASE_ATTACHMENTS) | learned
        view = rt.view(case.merchant_id)
        captures = [view.transactions_by_id[t].captured_at.date() for t in case.txn_ids if t in view.transactions_by_id]
        self._seq += 1
        claim = Claim(
            claim_id=f"CLM-{self._seq:05d}", case_id=case.case_id, merchant_id=case.merchant_id,
            proof_id=case.proof.proof_id, discrepancy_type=case.discrepancy_type, pattern=case.pattern,
            amount_paise=case.proof.discrepancy_paise, oldest_capture=min(captures) if captures else rt.clock.today(),
            attachments=attachments,
            evidence={"proof": case.proof.proof_id, "rules": list(case.proof.rule_ids),
                      "records": len(case.proof.source_records), "transactions": len(case.txn_ids)},
        )
        self.claims[claim.claim_id] = claim
        case.claim_id = claim.claim_id
        reference = rt.workflow.submit(claim)
        rt.state_machine.transition(
            case, S.FILED, ACTOR, f"Claim {claim.claim_id} filed for Rs {claim.amount_paise / 100:,.2f}",
            tool=f"workflow.{rt.workflow.name}.submit", action="claim filed", result=reference,
            evidence=sorted(attachments),
            learned_attachments=sorted(learned), memory_applied=bool(learned),
        )
        case.next_check_at = rt.clock.now() + CHECK_AFTER
        rt.state_machine.transition(case, S.WAITING, ACTOR, f"Waiting for the claims desk; next check {case.next_check_at:%d %b}",
                                    action="follow-up scheduled")
        rt.request_prevention(case)
        return claim

    # -- follow-through ---------------------------------------------------------

    def tick(self) -> None:
        rt = self.rt
        now = rt.clock.now()
        for case in rt.cases.in_state(S.WAITING):
            if case.next_check_at and now >= case.next_check_at:
                self._check(case)

    def _check(self, case: Case) -> None:
        rt = self.rt
        sm = rt.state_machine
        claim = self.claims[case.claim_id]
        response = rt.workflow.check(claim)

        if response is None:
            sm.transition(case, S.FOLLOW_UP, ACTOR, "No response from the claims desk by the due date",
                          tool=f"workflow.{rt.workflow.name}.check")
            case.follow_ups += 1
            if case.follow_ups > MAX_FOLLOW_UPS:
                case.escalation = {"reason": f"No response after {MAX_FOLLOW_UPS} follow-ups",
                                   "missing": ["counterparty response"], "agent_action": "Human escalation to acquirer",
                                   "claim": f"FILED as {claim.reference}", "disputed_paise": claim.amount_paise}
                sm.transition(case, S.ESCALATED, ACTOR, "Counterparty unresponsive; escalating", decision="escalate")
                return
            rt.workflow.follow_up(claim)
            case.next_check_at = rt.clock.now() + CHECK_AFTER
            sm.transition(case, S.WAITING, ACTOR, f"Follow-up {case.follow_ups} sent; next check {case.next_check_at:%d %b}",
                          tool=f"workflow.{rt.workflow.name}.follow_up", action="follow-up sent")
            return

        attachments = sorted(claim.attachments)
        if response.status in (ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_APPROVED):
            rt.memory.record_response(case.pattern, response.reason_code, attachments, str(response.status))
            claim.recovered_paise = response.approved_paise
            case.recovered_paise = response.approved_paise
            rt.events.append(ACTOR, "recovery.confirmed", case_id=case.case_id, merchant_id=case.merchant_id,
                             claim_id=claim.claim_id, recovered_paise=response.approved_paise,
                             reason="Reversal credit confirmed against the claim")
            if response.status is ClaimStatus.APPROVED:
                sm.transition(case, S.RECOVERED, ACTOR, f"Recovered Rs {response.approved_paise / 100:,.2f}",
                              result=response.message, decision="close")
            else:
                sm.transition(case, S.PARTIALLY_RECOVERED, ACTOR,
                              f"Partially recovered Rs {response.approved_paise / 100:,.2f} of Rs {claim.amount_paise / 100:,.2f}",
                              result=response.message)
                sm.transition(case, S.CLOSED, ACTOR, "Desk position is final for this return period")
            rt.confirm_prevention(case)
            rt.remember(case)
            return

        code = rt.reasoner.interpret_response(response.message) or response.reason_code
        rt.memory.record_response(case.pattern, code, attachments, "REJECTED")
        sm.transition(case, S.REJECTED, ACTOR, f"Claims desk: {response.message}", result=code)
        remedy = REMEDIES.get(code or "", None)
        if response.status is ClaimStatus.FINAL_REJECTED or code == "OUTSIDE_DISPUTE_WINDOW":
            sm.transition(case, S.CLOSED_UNRECOVERED, ACTOR, "Rejection cannot be answered with further evidence",
                          decision="close unrecovered")
            rt.remember(case)
            return
        if remedy and remedy not in claim.attachments and claim.attempts <= MAX_REPRESENTS:
            sm.transition(case, S.REPRESENT, ACTOR, f"Rejection {code} is answered by attaching {remedy}",
                          decision="represent", action=f"attach {remedy}")
            reference = rt.workflow.represent(claim, {remedy}, f"Re-presented with {remedy}")
            sm.transition(case, S.FILED, ACTOR, f"Re-presented as {reference}", tool=f"workflow.{rt.workflow.name}.represent")
            case.next_check_at = rt.clock.now() + CHECK_AFTER
            sm.transition(case, S.WAITING, ACTOR, f"Waiting for the claims desk; next check {case.next_check_at:%d %b}")
            return
        case.escalation = {"reason": f"Rejection {code} could not be answered automatically",
                           "missing": ["human review of counterparty position"], "agent_action": "Human verification requested",
                           "claim": f"REJECTED as {claim.reference}", "disputed_paise": claim.amount_paise}
        sm.transition(case, S.ESCALATED, ACTOR, "No automatic remedy for this rejection", decision="escalate")
        rt.remember(case)
