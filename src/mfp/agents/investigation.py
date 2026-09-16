"""Agent 2 -- the Investigation Agent.

Answers: what happened, and do we have enough evidence to establish it?

For each discovered case it gathers the records, the governing rules, the
rate-card history and precedent from memory; asks the Reasoner what the
findings most likely mean; submits a Candidate to the Proof Engine; and acts
on the verdict:

  PROVEN             -> ACTION_PENDING (or BATCHED below the materiality floor)
  UNPROVEN           -> ESCALATED, with the reason and the missing evidence
  NOT_A_DISCREPANCY  -> CLOSED

The Reasoner shapes the explanation. It has no path to the verdict: the
Candidate it helps build carries no amount, and only the Proof Engine can
produce a ProofResult.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mfp.cases.state_machine import Case
from mfp.core.enums import Actor, CaseState, Verdict
from mfp.schemas.proof import Candidate, EvidenceRef

if TYPE_CHECKING:
    from mfp.runtime.system import Runtime

ACTOR = Actor.INVESTIGATION_AGENT
S = CaseState


class InvestigationAgent:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def investigate(self, case: Case) -> Case:
        rt = self.rt
        view = rt.view(case.merchant_id)
        today = rt.observed_through()
        sm = rt.state_machine

        sm.transition(case, S.INVESTIGATING, ACTOR, "Case opened by Monitor; gathering records, rules and precedent",
                      tool="records.lookup", evidence=list(case.txn_ids[:5]))

        rule_note = case.rule_id
        if case.rule_id in rt.rules.by_id:
            rule = rt.rules.rule(case.rule_id)
            rule_note = f"{rule.rule_id}: {rule.name} ({rule.verification_status}, {rule.confidence})"
        snapshots = [f"{s.effective_from}: pricing MCC {s.pricing_mcc}, credit {s.card_credit_rate_percent}%"
                     for s in view.processor_config]
        agreement = max(view.agreements, key=lambda a: a.signed_on) if view.agreements else None
        query = {"case_id": case.case_id, "merchant_id": case.merchant_id, "pattern": case.pattern,
                 "discrepancy_type": case.discrepancy_type, "instrument": str(case.instrument) if case.instrument else None,
                 "rule_id": case.rule_id}
        case.similar_cases = rt.memory.similar_cases(query, k=3)
        context = {
            **query, "month": case.month, "component": case.component, "transactions": len(case.txn_ids),
            "detected_paise": case.detected_paise, "rule": rule_note, "merchant_mcc": view.merchant.registered_mcc,
            "acquirer": view.merchant.acquirer_id, "rate_card_history": snapshots,
            "agreement": (f"credit {agreement.card_credit_rate_percent}%, debit {agreement.card_debit_rate_percent}%, "
                          f"SLA T+{agreement.settlement_sla_days}") if agreement else None,
            "similar_cases": case.similar_cases,
        }
        reasoning = rt.reasoner.explain(context)
        case.rationale = reasoning.rationale
        case.reasoner = reasoning.source
        rt.events.append(ACTOR, "investigation.reasoned", case_id=case.case_id, merchant_id=case.merchant_id,
                         reasoner=reasoning.source, hypothesis=reasoning.hypothesis, checks=reasoning.checks,
                         precedent=[p["case_id"] for p in case.similar_cases], notes=reasoning.notes,
                         reason="Interpretation only; the verdict belongs to the Proof Engine")

        view_lines = [l for t in case.txn_ids[:10] for l in view.lines_by_txn.get(t, [])]
        candidate = Candidate(
            candidate_id=f"CAND-{case.case_id}", case_id=case.case_id, merchant_id=case.merchant_id,
            discrepancy_type=case.discrepancy_type, transaction_ids=case.txn_ids, as_of=today,
            rationale=reasoning.rationale, component=case.component, pattern=case.pattern,
            instrument=case.instrument, suggested_rule_ids=(case.rule_id,),
            evidence=tuple(EvidenceRef(record_type="settlement_line", record_id=l.line_id) for l in view_lines),
        )
        case.candidate = candidate
        sm.transition(case, S.CANDIDATE, ACTOR, reasoning.hypothesis, action="candidate assembled",
                      evidence=reasoning.checks)
        sm.transition(case, S.PROVING, ACTOR, "Submitting candidate to the deterministic proof gate",
                      tool="proof_engine.prove", action="request proof")

        proof = rt.proof.prove(candidate, view, today, proof_id=f"PRF-{case.case_id}")
        case.proof = proof
        rt.events.append(Actor.PROOF_ENGINE, "proof.completed", case_id=case.case_id, merchant_id=case.merchant_id,
                         verdict=str(proof.verdict), discrepancy_paise=proof.discrepancy_paise,
                         rule_ids=list(proof.rule_ids), records=len(proof.source_records),
                         unproven_reason=proof.unproven_reason)

        if proof.verdict is Verdict.PROVEN:
            sm.transition(case, S.PROVEN, Actor.PROOF_ENGINE,
                          f"Verified Rs {proof.discrepancy_paise / 100:,.2f} across {len(case.txn_ids)} transaction(s)",
                          evidence=list(proof.rule_ids), result=str(proof.verdict))
            floor = rt.rules.materiality.per_case_floor_paise
            if proof.discrepancy_paise < floor:
                sm.transition(case, S.BATCHED, ACTOR,
                              f"Below the Rs {floor / 100:.2f} per-case materiality floor; held for aggregate filing")
            else:
                sm.transition(case, S.ACTION_PENDING, ACTOR, "Proven and material; handing to Follow-up Agent",
                              decision="claim", action="queued for Follow-up Agent")
        elif proof.verdict is Verdict.UNPROVEN:
            sm.transition(case, S.UNPROVEN, Actor.PROOF_ENGINE, proof.unproven_reason or "not proven",
                          evidence=list(proof.missing_evidence), result=str(proof.verdict))
            case.escalation = {
                "reason": proof.unproven_reason,
                "missing": list(proof.missing_evidence),
                "agent_action": "Human verification requested",
                "claim": "NOT FILED",
                "disputed_paise": proof.discrepancy_paise,
            }
            sm.transition(case, S.ESCALATED, ACTOR, "Cannot be proven from published rules and observed records",
                          decision="do not claim", action="Human verification requested")
        else:
            sm.transition(case, S.NO_DISCREPANCY, Actor.PROOF_ENGINE, "Re-derivation shows no discrepancy",
                          result=str(proof.verdict))
            sm.transition(case, S.CLOSED, ACTOR, "No discrepancy; closed without action", decision="do not claim")

        rt.remember(case)
        rt.emit_signature(case)
        return case
