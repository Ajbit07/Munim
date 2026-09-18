"""Read models for the UI and the demo: everything a judge needs to see why the agent acted."""

from __future__ import annotations

from typing import Any

from mfp.core.enums import CaseState
from mfp.runtime.system import Runtime


def _rule(rt: Runtime, rule_id: str) -> dict[str, Any]:
    base = rule_id.split(":")[0]
    if rule_id.startswith("AGREEMENT"):
        return {"rule_id": rule_id, "name": "Signed merchant agreement rate", "source": "Merchant agreement",
                "status": "CONTRACT", "confidence": "HIGH"}
    if base in rt.rules.by_id:
        r = rt.rules.rule(base)
        return {"rule_id": r.rule_id, "name": r.name, "source": f"{r.source.authority}: {r.source.document}",
                "url": r.source.url, "status": str(r.verification_status), "confidence": str(r.confidence),
                "effective_from": r.effective_from.isoformat(),
                "effective_to": r.effective_to.isoformat() if r.effective_to else None,
                "assumptions": list(r.assumptions)}
    return {"rule_id": rule_id, "name": rule_id.replace(".", " ").lower(), "source": "Reconciliation invariant",
            "status": "INVARIANT", "confidence": "HIGH"}


def case_detail(rt: Runtime, case_id: str, sample: int = 8) -> dict[str, Any]:
    case = rt.cases.get(case_id)
    view = rt.view(case.merchant_id)
    txns, lines, batches, credits = [], [], {}, {}
    for txn_id in case.txn_ids[:sample]:
        t = view.transactions_by_id.get(txn_id)
        if t:
            txns.append({"txn_id": t.txn_id, "kind": str(t.kind), "instrument": str(t.instrument),
                         "amount_paise": t.amount_paise, "captured_at": t.captured_at.isoformat()})
        if t is None and txn_id in view.lines_by_charge:
            device = view.devices_by_id.get(txn_id.split(":")[0])
            if device is not None:
                txns.append({"txn_id": txn_id, "kind": "DEVICE RENTAL", "instrument": device.device_type,
                             "amount_paise": device.monthly_rental_paise,
                             "captured_at": f"{txn_id.rsplit(':', 1)[1]}-01T00:00:00",
                             "device": {"device_id": device.device_id, "activated_on": device.activated_on.isoformat(),
                                        "rental_free_until": device.rental_free_until.isoformat(),
                                        "returned_on": device.returned_on.isoformat() if device.returned_on else None,
                                        "return_ref": device.return_ref}})
        for l in view.lines_by_txn.get(txn_id, []) or view.lines_by_charge.get(txn_id, []):
            lines.append({"line_id": l.line_id, "batch_id": l.batch_id, "txn_id": l.txn_id, "type": str(l.line_type),
                          "gross_paise": l.gross_paise, "mdr_paise": l.mdr_paise, "gst_paise": l.gst_paise,
                          "tcs_paise": l.tcs_paise, "tds_paise": l.tds_paise, "net_paise": l.net_paise})
            b = view.batches_by_id.get(l.batch_id)
            if b:
                batches[b.batch_id] = {"batch_id": b.batch_id, "settlement_date": b.settlement_date.isoformat(),
                                       "line_count": b.line_count, "net_paise": b.net_paise, "utr": b.utr}
                c = view.credits_by_utr.get(b.utr or "")
                if c:
                    credits[c.credit_id] = {"credit_id": c.credit_id, "value_date": c.value_date.isoformat(),
                                            "amount_paise": c.amount_paise, "utr": c.utr, "narration": c.narration}
    proof = case.proof
    claim = rt.followup.claims.get(case.claim_id) if case.claim_id else None
    rc = next((r for r in rt.root_causes(case.merchant_id) if r.root_cause_id == case.root_cause_id), None)
    rule_ids = list(proof.rule_ids) if proof else [case.rule_id]
    return {
        "case": {**case.summary(), "rationale": case.rationale, "reasoner": case.reasoner,
                 "opened_at": case.opened_at, "txn_count": len(case.txn_ids)},
        "transactions": txns, "lines": lines, "batches": list(batches.values()), "credits": list(credits.values()),
        "rules": [_rule(rt, r) for r in dict.fromkeys(rule_ids)],
        "proof": None if proof is None else {
            "proof_id": proof.proof_id, "verdict": str(proof.verdict), "expected_paise": proof.expected_paise,
            "actual_paise": proof.actual_paise, "discrepancy_paise": proof.discrepancy_paise,
            "computation": [s.model_dump() for s in proof.computation], "computed_at": proof.computed_at,
            "input_hash": proof.input_hash, "records": len(proof.source_records),
            "unproven_reason": proof.unproven_reason, "missing_evidence": list(proof.missing_evidence),
            "authorises_claim": proof.authorises_claim,
        },
        "claim": claim.summary() if claim else None,
        "root_cause": rc.summary() if rc else None,
        "similar_cases": [{k: p.get(k) for k in ("case_id", "pattern", "month", "state", "outcome", "similarity")}
                          for p in case.similar_cases],
        "history": [t.__dict__ for t in case.history],
    }


def late_settlements(rt: Runtime, merchant_id: str | None = None) -> dict[str, Any]:
    breaches = [b for mid, bs in rt.sla_breaches.items() if merchant_id in (None, mid) for b in bs]
    by_month: dict[str, dict[str, int]] = {}
    for b in breaches:
        month = f"{b.settled_on:%Y-%m}"
        row = by_month.setdefault(month, {"payments": 0, "held_up_paise": 0, "worst_days_late": 0})
        row["payments"] += 1
        row["held_up_paise"] += b.net_paise
        row["worst_days_late"] = max(row["worst_days_late"], b.banking_days_late)
    recent = sorted(breaches, key=lambda b: (b.settled_on, b.txn_id), reverse=True)[:25]
    return {
        "payments": len(breaches), "held_up_paise": sum(b.net_paise for b in breaches),
        "average_days_late": round(sum(b.banking_days_late for b in breaches) / len(breaches), 1) if breaches else 0,
        "by_month": dict(sorted(by_month.items())),
        "recent": [{**b.__dict__, "captured_on": b.captured_on.isoformat(), "due_on": b.due_on.isoformat(),
                    "deadline": b.deadline.isoformat(), "settled_on": b.settled_on.isoformat()} for b in recent],
    }


def human_queue(rt: Runtime) -> list[dict[str, Any]]:
    out = []
    for case in rt.cases.in_state(CaseState.ESCALATED):
        out.append({**case.summary(), "escalation": case.escalation})
    return sorted(out, key=lambda c: -(c["escalation"] or {}).get("disputed_paise", 0))
