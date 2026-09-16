"""The Reconciliation Engine.

Reconstructs the chain for one merchant:

    transaction -> settlement line -> settlement batch -> bank credit (by UTR)

and reports every place the chain does not hold as a Finding. A Finding is a
detection, not a conclusion: it carries no authority to claim. The Proof
Engine re-derives every finding from the raw records before a case may act.

Checks, per merchant:

  batch integrity     batch totals equal the sum of their lines
  credit matching     every positive batch has a bank credit with the same UTR
                      and amount; unrelated credits are ignored, not assumed
  L1-L4               payment deductions decomposed against expected charges
  L5                  refund/chargeback debits appearing more than once, or
                      with no refund event in the ledger
  L6                  successful payments absent from every batch after their
                      contracted settlement date plus grace
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time

from mfp.core.enums import Instrument
from mfp.data.store import MerchantView
from mfp.fees.engine import ByPolicy, FeeEngine, Unresolved
from mfp.reconciliation.calendar import add_banking_days, settlement_cycle
from mfp.schemas.ledger import LineType, TxnKind, TxnStatus


@dataclass
class Finding:
    merchant_id: str
    discrepancy_type: str
    pattern: str
    component: str                      # MDR | GST | TAX | REFUND_DEBIT | SETTLEMENT | UNRESOLVED
    txn_id: str
    captured_on: date
    instrument: Instrument | None
    rule_id: str
    amount: ByPolicy                    # positive = merchant is owed
    line_ids: tuple[str, ...] = ()
    batch_ids: tuple[str, ...] = ()
    assumed: bool = False
    unresolved_reason: str | None = None

    @property
    def month(self) -> str:
        return f"{self.captured_on:%Y-%m}"

    @property
    def group_key(self) -> tuple:
        return (self.merchant_id, self.discrepancy_type, self.pattern, str(self.instrument or "-"),
                self.rule_id.split(":")[0], self.month, self.assumed, self.unresolved_reason is not None)


@dataclass
class IntegrityIssue:
    kind: str
    batch_id: str
    detail: str


@dataclass
class ReconciliationReport:
    merchant_id: str
    as_of: date
    transactions_scanned: int = 0
    lines_scanned: int = 0
    batches_scanned: int = 0
    credits_matched: int = 0
    unrelated_credits: int = 0
    awaiting_cycle: int = 0
    findings: list[Finding] = field(default_factory=list)
    integrity_issues: list[IntegrityIssue] = field(default_factory=list)
    months: dict[str, dict[str, int]] = field(default_factory=dict)


class ReconciliationEngine:
    def __init__(self, fees: FeeEngine, settlement_rules: dict) -> None:
        self.fees = fees
        self.cutoff = time.fromisoformat(settlement_rules["default_merchant_sla"]["cutoff_local_time"])
        self.default_sla = settlement_rules["default_merchant_sla"]["settlement_days"]
        self.grace = settlement_rules["lifecycle"]["unsettled_after_sla_days"]

    def due_date(self, view: MerchantView, captured_at) -> date:
        agreement = self.fees.agreement_on(view.agreements, captured_at.date())
        sla = agreement.settlement_sla_days if agreement else self.default_sla
        return add_banking_days(settlement_cycle(captured_at, self.cutoff), sla)

    def reconcile(self, view: MerchantView, as_of: date, *, settled_after: date | None = None) -> ReconciliationReport:
        """Reconcile batches settled in (settled_after, as_of], and payments falling due in that window.

        With settled_after=None this is a full historical reconciliation. With a
        date it is the Monitor's incremental pass: only newly settled batches and
        newly overdue payments are examined, against the full history before them.
        """
        through = as_of
        report = ReconciliationReport(view.merchant_id, as_of)
        merchant = view.merchant
        visible = [b for b in view.settlement_batches if b.settlement_date <= through]
        visible_ids = {b.batch_id for b in visible}
        batches = [b for b in visible if settled_after is None or b.settlement_date > settled_after]
        batch_ids = visible_ids
        report.batches_scanned = len(batches)
        months: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # -- batch integrity and bank matching --------------------------------
        batch_utrs = set()
        for batch in batches:
            lines = view.lines_by_batch.get(batch.batch_id, [])
            months[f"{batch.settlement_date:%Y-%m}"]["batches"] += 1
            for field_name in ("gross_paise", "mdr_paise", "gst_paise", "tcs_paise", "tds_paise", "net_paise"):
                total = sum(getattr(line, field_name) for line in lines)
                if total != getattr(batch, field_name):
                    report.integrity_issues.append(IntegrityIssue(
                        "BATCH_TOTAL_MISMATCH", batch.batch_id,
                        f"{field_name}: batch {getattr(batch, field_name)} vs lines {total}"))
            if batch.net_paise > 0:
                credit = view.credits_by_utr.get(batch.utr or "")
                if credit is None or credit.amount_paise != batch.net_paise:
                    report.integrity_issues.append(IntegrityIssue(
                        "CREDIT_MISMATCH", batch.batch_id,
                        f"expected bank credit {batch.net_paise} paise under UTR {batch.utr}; "
                        f"found {credit.amount_paise if credit else 'none'}"))
                else:
                    report.credits_matched += 1
                    batch_utrs.add(batch.utr)
        report.unrelated_credits = sum(
            1 for c in view.bank_credits if c.value_date <= through and c.utr not in batch_utrs
            and not c.narration.startswith("PG SETTLEMENT"))

        # -- payments: L1 to L4 ----------------------------------------------
        refund_lines: dict[str, list] = defaultdict(list)
        for batch in batches:
            for line in view.lines_by_batch.get(batch.batch_id, []):
                report.lines_scanned += 1
                months[f"{batch.settlement_date:%Y-%m}"]["lines"] += 1
                if line.line_type in (LineType.REFUND, LineType.CHARGEBACK):
                    refund_lines[line.txn_id].append(line)
                    continue
                if line.line_type is not LineType.PAYMENT or line.instrument is None:
                    continue
                on = line.captured_at.date()
                result = self.fees.decompose(
                    line.instrument, line.gross_paise, on, merchant, view.agreements,
                    mdr=line.mdr_paise, gst=line.gst_paise, tcs=line.tcs_paise, tds=line.tds_paise,
                )
                if isinstance(result, Unresolved):
                    actual = line.mdr_paise + line.gst_paise + line.tcs_paise + line.tds_paise
                    report.findings.append(Finding(
                        view.merchant_id,
                        "L2_NIL_MDR_VIOLATION" if line.instrument in (Instrument.UPI_P2M_BANK, Instrument.RUPAY_DEBIT)
                        else "L1_WRONG_MDR_BAND",
                        "mdr_above_mcc_rate", "UNRESOLVED", line.txn_id, on, line.instrument,
                        f"UNRESOLVED:{result.stage}", ByPolicy.const(actual), (line.line_id,), (batch.batch_id,),
                        unresolved_reason=result.reason,
                    ))
                    continue
                for comp in result:
                    report.findings.append(Finding(
                        view.merchant_id, comp.discrepancy_type, comp.pattern, comp.component, line.txn_id, on,
                        line.instrument, comp.rule.rule_id, comp.amount, (line.line_id,), (batch.batch_id,),
                        assumed=comp.assumed,
                    ))

        # -- refunds: L5 -----------------------------------------------------
        scanned_ids = {b.batch_id for b in batches}
        for txn_id in refund_lines:
            txn = view.transactions_by_id.get(txn_id)
            history = [l for l in view.lines_by_txn.get(txn_id, [])
                       if l.batch_id in visible_ids and l.line_type in (LineType.REFUND, LineType.CHARGEBACK)]
            ordered = sorted(history, key=lambda l: (view.batches_by_id[l.batch_id].settlement_date, l.line_id))
            if txn is None or txn.kind is TxnKind.PAYMENT:
                suspects = ordered
                pattern = "refund_without_refund_event"
            else:
                suspects = ordered[1:]
                pattern = "refund_debited_twice"
            for line in suspects:
                if line.batch_id in scanned_ids:
                    report.findings.append(self._refund_finding(view, line, pattern, ordered))

        # -- unsettled: L6 ---------------------------------------------------
        for txn in view.transactions:
            if txn.captured_at.date() > through:
                continue  # has not happened yet from the agent's point of view
            report.transactions_scanned += 1
            if txn.kind is not TxnKind.PAYMENT or txn.status is not TxnStatus.SUCCESS:
                continue
            if any(l.batch_id in batch_ids for l in view.lines_by_txn.get(txn.txn_id, [])):
                continue
            due = self.due_date(view, txn.captured_at)
            deadline = add_banking_days(due, self.grace)
            if deadline > through:
                report.awaiting_cycle += 1
                continue
            if settled_after is not None and deadline <= settled_after:
                continue  # already examined in an earlier pass
            owed = self.fees.expected_net(txn.instrument, txn.amount_paise, txn.captured_at.date(),
                                          merchant, view.agreements)
            amount = owed if not isinstance(owed, Unresolved) else ByPolicy.const(txn.amount_paise)
            report.findings.append(Finding(
                view.merchant_id, "L6_UNSETTLED_TRANSACTION", "payment_missing_from_settlement", "SETTLEMENT",
                txn.txn_id, txn.captured_at.date(), txn.instrument, "RECON.PAYMENT.SETTLED_ONCE", amount,
                unresolved_reason=None if not isinstance(owed, Unresolved) else owed.reason,
            ))

        for finding in report.findings:
            months[finding.month]["findings"] += 1
        report.months = {k: dict(v) for k, v in sorted(months.items())}
        return report

    @staticmethod
    def _refund_finding(view: MerchantView, line, pattern: str, all_lines) -> Finding:
        return Finding(
            view.merchant_id, "L5_ORPHAN_REFUND", pattern, "REFUND_DEBIT", line.txn_id,
            view.batches_by_id[line.batch_id].settlement_date, line.instrument,
            "RECON.REFUND.SINGLE_DEBIT", ByPolicy.const(-line.gross_paise),
            tuple(l.line_id for l in all_lines), tuple(l.batch_id for l in all_lines),
        )
