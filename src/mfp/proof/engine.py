"""The Proof Engine -- the financial authority.

Nothing becomes a claim without passing through prove(). The engine takes a
Candidate, which carries no amount and no authority, and re-derives every
referenced transaction from the raw observed records. It does not reuse the
Reconciliation Engine's findings; it looks the records up again by id.

A Candidate is PROVEN only if, for every transaction it names:

  1. the evidence chain is complete -- the ledger event exists, the settlement
     line exists where it should (or is absent where it should be), its batch
     totals reconcile, and a positive batch traces to a bank credit by UTR
  2. every governing rule resolved, and none is ASSUMED
  3. the discrepancy is positive under EVERY rounding policy, so it does not
     depend on the assumed rounding convention

The proven amount is the sum of the per-transaction minimum across rounding
policies: the smallest amount that is true however the processor rounded.

Otherwise the verdict is UNPROVEN with a reason and a list of missing evidence
for the human queue, or NOT_A_DISCREPANCY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from mfp.core.clock import Clock
from mfp.core.enums import Verdict
from mfp.data.store import MerchantView
from mfp.fees.engine import ByPolicy, FeeEngine, Unresolved
from mfp.reconciliation.calendar import add_banking_days
from mfp.reconciliation.engine import ReconciliationEngine
from mfp.schemas.ledger import LineType, TxnKind, TxnStatus
from mfp.schemas.proof import Candidate, ComputationStep, EvidenceRef, ProofResult

ENGINE_VERSION = "1.0.0"
MAX_DETAILED_STEPS = 12


@dataclass
class _TxnProof:
    txn_id: str
    amount: ByPolicy | None = None
    actual_charged: int = 0
    rule_ids: list[str] = field(default_factory=list)
    records: list[EvidenceRef] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unresolved: str | None = None
    assumed: list[str] = field(default_factory=list)
    step: ComputationStep | None = None
    notes: list[str] = field(default_factory=list)


class ProofEngine:
    def __init__(self, fees: FeeEngine, recon: ReconciliationEngine, clock: Clock) -> None:
        self.fees = fees
        self.recon = recon
        self.clock = clock

    # -- evidence ------------------------------------------------------------

    def _trace_batch(self, view: MerchantView, batch_id: str, tp: _TxnProof) -> None:
        batch = view.batches_by_id.get(batch_id)
        if batch is None:
            tp.missing.append(f"settlement batch {batch_id}")
            return
        tp.records.append(EvidenceRef(record_type="settlement_batch", record_id=batch_id,
                                      note=f"settled {batch.settlement_date}, net {batch.net_paise} paise"))
        lines = view.lines_by_batch.get(batch_id, [])
        if sum(l.net_paise for l in lines) != batch.net_paise:
            tp.missing.append(f"batch {batch_id} totals do not reconcile to its lines")
        if batch.net_paise > 0:
            credit = view.credits_by_utr.get(batch.utr or "")
            if credit is None or credit.amount_paise != batch.net_paise:
                tp.missing.append(f"bank credit for batch {batch_id} under UTR {batch.utr}")
            else:
                tp.records.append(EvidenceRef(record_type="bank_credit", record_id=credit.credit_id,
                                              note=f"UTR {credit.utr}, {credit.amount_paise} paise on {credit.value_date}"))

    # -- per component ---------------------------------------------------------

    def _prove_charge(self, c: Candidate, view: MerchantView, txn_id: str) -> _TxnProof:
        tp = _TxnProof(txn_id)
        txn = view.transactions_by_id.get(txn_id)
        if txn is None:
            tp.missing.append(f"ledger transaction {txn_id}")
            return tp
        tp.records.append(EvidenceRef(record_type="transaction", record_id=txn_id,
                                      note=f"{txn.instrument} {txn.amount_paise} paise at {txn.captured_at}"))
        lines = [l for l in view.lines_by_txn.get(txn_id, []) if l.line_type is LineType.PAYMENT]
        if len(lines) != 1:
            tp.missing.append(f"exactly one settlement line for {txn_id} (found {len(lines)})")
            return tp
        line = lines[0]
        tp.records.append(EvidenceRef(record_type="settlement_line", record_id=line.line_id,
                                      note=f"MDR {line.mdr_paise}, GST {line.gst_paise}, TCS {line.tcs_paise}, TDS {line.tds_paise}"))
        self._trace_batch(view, line.batch_id, tp)
        if line.gross_paise != txn.amount_paise or line.instrument != txn.instrument:
            tp.missing.append(f"settlement line {line.line_id} disagrees with ledger on amount or instrument")
            return tp

        on = txn.captured_at.date()
        snap = view.snapshot_on(on)
        if snap is not None:
            tp.records.append(EvidenceRef(record_type="config_snapshot", record_id=snap.snapshot_id,
                                          note=f"pricing MCC {snap.pricing_mcc}, credit {snap.card_credit_rate_percent}%"))
        result = self.fees.decompose(txn.instrument, txn.amount_paise, on, view.merchant, view.agreements,
                                     mdr=line.mdr_paise, gst=line.gst_paise, tcs=line.tcs_paise, tds=line.tds_paise)
        if isinstance(result, Unresolved):
            tp.unresolved = result.reason
            return tp
        match = next((comp for comp in result if comp.component == c.component), None)
        if match is None:
            tp.amount = ByPolicy.const(0)
            return tp
        tp.amount = match.amount
        tp.actual_charged = match.actual_paise
        tp.rule_ids.append(match.rule.rule_id)
        if match.assumed:
            tp.assumed.append(match.rule.rule_id)
        cfg = self.fees.configured_policy
        tp.step = ComputationStep(
            label=f"{txn_id} {c.component}",
            expression=(f"charged {match.actual_paise} - expected {match.expected[cfg]} "
                        f"under {match.rule.rule_id} ({match.rule.description}); "
                        f"range across rounding policies {match.amount.low}..{match.amount.high}"),
            result_paise=match.amount.low, rule_id=match.rule.rule_id,
        )
        return tp

    def _prove_refund(self, c: Candidate, view: MerchantView, txn_id: str) -> _TxnProof:
        tp = _TxnProof(txn_id)
        lines = sorted((l for l in view.lines_by_txn.get(txn_id, [])
                        if l.line_type in (LineType.REFUND, LineType.CHARGEBACK)),
                       key=lambda l: (view.batches_by_id[l.batch_id].settlement_date if l.batch_id in view.batches_by_id else date.min, l.line_id))
        txn = view.transactions_by_id.get(txn_id)
        for line in lines:
            tp.records.append(EvidenceRef(record_type="settlement_line", record_id=line.line_id,
                                          note=f"debit {line.gross_paise} paise"))
            self._trace_batch(view, line.batch_id, tp)
        tp.actual_charged = sum(-l.gross_paise for l in lines)
        tp.rule_ids.append("RECON.REFUND.SINGLE_DEBIT")
        if txn is not None and txn.kind in (TxnKind.REFUND, TxnKind.CHARGEBACK):
            tp.records.append(EvidenceRef(record_type="transaction", record_id=txn_id,
                                          note=f"{txn.kind} of {txn.amount_paise} paise for {txn.parent_txn_id}"))
            owed = max(0, tp.actual_charged - txn.amount_paise)
            tp.amount = ByPolicy.const(owed)
            expression = f"debited {len(lines)} times x {txn.amount_paise} - one legitimate debit"
        elif txn is None:
            tp.amount = ByPolicy.const(tp.actual_charged)
            expression = f"{len(lines)} debit(s) with no refund or chargeback event in the ledger"
        else:
            tp.missing.append(f"{txn_id} is a payment, not a refund event")
            return tp
        tp.step = ComputationStep(label=f"{txn_id} REFUND_DEBIT", expression=expression,
                                  result_paise=tp.amount.low, rule_id="RECON.REFUND.SINGLE_DEBIT")
        return tp

    def _prove_unsettled(self, c: Candidate, view: MerchantView, txn_id: str, as_of: date) -> _TxnProof:
        tp = _TxnProof(txn_id)
        txn = view.transactions_by_id.get(txn_id)
        if txn is None or txn.kind is not TxnKind.PAYMENT or txn.status is not TxnStatus.SUCCESS:
            tp.missing.append(f"successful captured payment {txn_id}")
            return tp
        tp.records.append(EvidenceRef(record_type="transaction", record_id=txn_id,
                                      note=f"{txn.instrument} {txn.amount_paise} paise captured {txn.captured_at}"))
        if view.lines_by_txn.get(txn_id):
            tp.amount = ByPolicy.const(0)  # it did settle somewhere
            return tp
        due = self.recon.due_date(view, txn.captured_at)
        deadline = add_banking_days(due, self.recon.grace)
        if deadline > as_of:
            tp.amount = ByPolicy.const(0)
            tp.notes.append(f"not yet due: contracted settlement {due}, grace until {deadline}")
            return tp
        later = [b for b in view.settlement_batches if b.settlement_date > deadline]
        if not later:
            tp.missing.append("a settlement batch after the due date, to show settlement continued without this payment")
            return tp
        tp.records.append(EvidenceRef(record_type="settlement_search", record_id=f"{view.merchant_id}:all-batches",
                                      note=f"searched {len(view.settlement_batches)} batches; none contain {txn_id}; "
                                           f"{len(later)} batches settled after the {deadline} deadline"))
        agreement = self.fees.agreement_on(view.agreements, txn.captured_at.date())
        if agreement:
            tp.records.append(EvidenceRef(record_type="agreement", record_id=agreement.agreement_id,
                                          note=f"settlement SLA {agreement.settlement_sla_days} banking day(s)"))
        exp = self.fees.expected(txn.instrument, txn.amount_paise, txn.captured_at.date(), view.merchant, view.agreements)
        if isinstance(exp, Unresolved):
            tp.unresolved = f"owed net cannot be computed: {exp.reason}"
            return tp
        tp.amount = ByPolicy.const(txn.amount_paise) - exp.total
        tp.rule_ids.extend(["RECON.PAYMENT.SETTLED_ONCE", exp.mdr_rule.rule_id])
        if exp.assumed_rules:
            # Existence of the discrepancy does not depend on the assumption; only
            # how much of the gross the processor may keep does. Keeping the assumed
            # charge makes the claimed net conservative.
            tp.notes.append(f"owed net deducts charges under ASSUMED {', '.join(exp.assumed_rules)}; claim is conservative")
        tp.step = ComputationStep(
            label=f"{txn_id} SETTLEMENT",
            expression=f"gross {txn.amount_paise} - expected charges {exp.total[self.fees.configured_policy]}; "
                       f"due {due}, grace to {deadline}, absent from every batch",
            result_paise=tp.amount.low, rule_id="RECON.PAYMENT.SETTLED_ONCE",
        )
        return tp

    # -- verdict ---------------------------------------------------------------

    def prove(self, candidate: Candidate, view: MerchantView, as_of: date, proof_id: str) -> ProofResult:
        proofs: list[_TxnProof] = []
        for txn_id in candidate.transaction_ids:
            if candidate.component in ("MDR", "GST", "TAX"):
                proofs.append(self._prove_charge(candidate, view, txn_id))
            elif candidate.component == "REFUND_DEBIT":
                proofs.append(self._prove_refund(candidate, view, txn_id))
            elif candidate.component == "SETTLEMENT":
                proofs.append(self._prove_unsettled(candidate, view, txn_id, as_of))
            else:
                tp = _TxnProof(txn_id)
                tp.unresolved = f"component {candidate.component!r} has no deterministic proof"
                proofs.append(tp)

        missing = [m for tp in proofs for m in tp.missing]
        unresolved = [f"{tp.txn_id}: {tp.unresolved}" for tp in proofs if tp.unresolved]
        assumed = sorted({r for tp in proofs for r in tp.assumed})
        rounding_dependent = [tp.txn_id for tp in proofs if tp.amount and tp.amount.low <= 0 < tp.amount.high]
        clean = [tp for tp in proofs if tp.amount is not None]
        discrepant = [tp for tp in clean if tp.amount.low > 0]
        not_discrepant = [tp for tp in clean if tp.amount.high <= 0]

        reason = None
        if missing:
            reason = f"evidence incomplete for {len({tp.txn_id for tp in proofs if tp.missing})} transaction(s)"
        elif unresolved:
            reason = "a governing rule could not be resolved: " + unresolved[0]
        elif assumed:
            reason = (f"the charge rests on {', '.join(assumed)}, which is ASSUMED; "
                      "whether it is owed cannot be established from published rules")
        elif rounding_dependent:
            reason = (f"{len(rounding_dependent)} transaction(s) are discrepant only under some rounding "
                      "conventions; the rounding rule is ASSUMED")
        elif discrepant and not_discrepant:
            reason = (f"{len(not_discrepant)} of {len(proofs)} transactions show no discrepancy on re-derivation; "
                      "the candidate is not homogeneous")

        if reason is not None:
            verdict = Verdict.UNPROVEN
            disputed = sum(max(tp.amount.high, 0) for tp in clean) if clean else 0
            if not disputed:
                disputed = sum(tp.actual_charged for tp in proofs)
            amount, actual = disputed, 0
        elif not discrepant:
            verdict, amount, actual = Verdict.NOT_A_DISCREPANCY, 0, 0
        else:
            verdict = Verdict.PROVEN
            amount = sum(tp.amount.low for tp in discrepant)
            actual = -sum(tp.actual_charged for tp in discrepant)

        steps = [tp.step for tp in proofs if tp.step is not None]
        detailed = steps[:MAX_DETAILED_STEPS]
        if len(steps) > MAX_DETAILED_STEPS:
            detailed.append(ComputationStep(label=f"... {len(steps) - MAX_DETAILED_STEPS} more transactions",
                                            expression="each re-derived identically", result_paise=None))
        detailed.append(ComputationStep(
            label="total", expression=f"sum over {len(proofs)} transactions of the minimum across rounding policies",
            result_paise=amount))
        records: list[EvidenceRef] = []
        seen: set[tuple[str, str]] = set()
        for tp in proofs:
            for ref in tp.records:
                if (ref.record_type, ref.record_id) not in seen:
                    seen.add((ref.record_type, ref.record_id))
                    records.append(ref)

        return ProofResult(
            proof_id=proof_id, case_id=candidate.case_id, candidate_id=candidate.candidate_id,
            merchant_id=candidate.merchant_id, discrepancy_type=candidate.discrepancy_type,
            verdict=verdict, expected_paise=actual + amount, actual_paise=actual, discrepancy_paise=amount,
            rule_ids=tuple(dict.fromkeys(r for tp in proofs for r in tp.rule_ids)),
            computation=tuple(detailed), source_records=tuple(records),
            computed_at=self.clock.now().isoformat(), input_hash=candidate.input_hash(),
            engine_version=ENGINE_VERSION, unproven_reason=reason,
            missing_evidence=tuple(dict.fromkeys(missing + [a + " is ASSUMED" for a in assumed]))[:20],
        )
