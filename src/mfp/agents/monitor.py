"""Agent 1 -- the Monitor Agent.

Answers: what needs attention right now?

Nobody asks it to run. When a merchant connects it launches the 12-month
historical audit on its own. After that, every clock tick it looks for newly
settled batches and newly overdue payments, reconciles only what changed, and
opens a case for every group of findings no existing case already owns.

It does not judge findings. Opening a case is a statement that something needs
investigating, not that anything is owed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from mfp.cases.state_machine import Case
from mfp.core.enums import Actor
from mfp.reconciliation.engine import Finding, ReconciliationReport

if TYPE_CHECKING:
    from mfp.runtime.system import Runtime

ACTOR = Actor.MONITOR_AGENT


class MonitorAgent:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.watermarks: dict[str, date] = {}

    # -- onboarding and backfill ------------------------------------------------

    def connect(self, merchant_id: str) -> ReconciliationReport:
        rt = self.rt
        view = rt.view(merchant_id)
        m = view.merchant
        rt.events.append(ACTOR, "merchant.connected", merchant_id=merchant_id, legal_name=m.legal_name,
                         mcc=m.registered_mcc, acquirer=m.acquirer_id, open_complaints=0,
                         reason="Merchant connected; no complaint or request has been raised")
        return self.backfill(merchant_id)

    def backfill(self, merchant_id: str) -> ReconciliationReport:
        rt = self.rt
        view = rt.view(merchant_id)
        today = rt.observed_through()
        batches = [b for b in view.settlement_batches if b.settlement_date <= today]
        if not batches:
            rt.events.append(ACTOR, "backfill.skipped", merchant_id=merchant_id, reason="no settlement history")
            self.watermarks[merchant_id] = today
            return ReconciliationReport(merchant_id, today)
        first, last = batches[0].settlement_date, batches[-1].settlement_date
        calendar_months = (last.year - first.year) * 12 + last.month - first.month + 1
        months = max(1, round((last - first).days / 30.44))
        rt.events.append(ACTOR, "backfill.started", merchant_id=merchant_id, months=months,
                         calendar_months=calendar_months,
                         first_settlement=first.isoformat(), last_settlement=last.isoformat(),
                         reason=f"New merchant: auditing {months} months of settlement history before waiting for new activity",
                         tool="reconciliation_engine.reconcile")
        report = rt.recon.reconcile(view, today)
        for month, stats in report.months.items():
            rt.events.append(ACTOR, "backfill.month.scanned", merchant_id=merchant_id, month=month,
                             batches=stats.get("batches", 0), lines=stats.get("lines", 0),
                             findings=stats.get("findings", 0))
        cases = self.open_cases(merchant_id, report, source="backfill")
        rt.events.append(ACTOR, "backfill.completed", merchant_id=merchant_id, months=months,
                         calendar_months=calendar_months,
                         transactions=report.transactions_scanned, lines=report.lines_scanned,
                         batches=report.batches_scanned, credits_matched=report.credits_matched,
                         unrelated_credits_ignored=report.unrelated_credits, awaiting_cycle=report.awaiting_cycle,
                         integrity_issues=len(report.integrity_issues), findings=len(report.findings),
                         cases_opened=len(cases))
        self.watermarks[merchant_id] = today
        return report

    # -- continuous monitoring --------------------------------------------------

    def tick(self, merchant_id: str) -> list[Case]:
        rt = self.rt
        view = rt.view(merchant_id)
        today = rt.observed_through()
        since = self.watermarks.get(merchant_id)
        if since is None or today <= since:
            return []
        fresh = [b for b in view.settlement_batches if since < b.settlement_date <= today]
        for batch in fresh:
            credit = view.credits_by_utr.get(batch.utr or "")
            rt.events.append(ACTOR, "settlement.batch.observed", merchant_id=merchant_id, batch_id=batch.batch_id,
                             settlement_date=batch.settlement_date.isoformat(), lines=batch.line_count,
                             net_paise=batch.net_paise, bank_credit=credit.credit_id if credit else None,
                             reason="New settlement batch detected")
        report = rt.recon.reconcile(view, today, settled_after=since)
        self.watermarks[merchant_id] = today
        if fresh or report.findings:
            rt.events.append(ACTOR, "reconciliation.completed", merchant_id=merchant_id,
                             batches=report.batches_scanned, lines=report.lines_scanned,
                             credits_matched=report.credits_matched, findings=len(report.findings),
                             tool="reconciliation_engine.reconcile")
        return self.open_cases(merchant_id, report, source=f"tick:{today}")

    # -- case creation ----------------------------------------------------------

    def open_cases(self, merchant_id: str, report: ReconciliationReport, source: str) -> list[Case]:
        rt = self.rt
        for issue in report.integrity_issues:
            rt.events.append(ACTOR, "integrity.issue", merchant_id=merchant_id, kind=issue.kind,
                             batch_id=issue.batch_id, detail=issue.detail)
        covered = rt.cases.covered()
        groups: dict[tuple, list[Finding]] = defaultdict(list)
        for finding in report.findings:
            if (finding.txn_id, finding.component) not in covered:
                groups[finding.group_key].append(finding)

        opened: list[Case] = []
        for key, findings in sorted(groups.items(), key=lambda kv: (kv[0][5], kv[0][2])):
            head = findings[0]
            detected = sum(f.amount[rt.fees.configured_policy] for f in findings)
            case = Case(
                case_id=rt.cases.next_id(), merchant_id=merchant_id, discrepancy_type=head.discrepancy_type,
                pattern=head.pattern, component=head.component, instrument=head.instrument, rule_id=head.rule_id,
                month=head.month, txn_ids=tuple(sorted({f.txn_id for f in findings})),
                opened_at=rt.clock.now().isoformat(), detected_paise=detected, assumed=head.assumed,
                unresolved_reason=head.unresolved_reason,
            )
            rt.cases.add(case, key + (source,))
            rt.events.append(
                ACTOR, "case.discovered", case_id=case.case_id, merchant_id=merchant_id, source=source,
                discrepancy_type=case.discrepancy_type, pattern=case.pattern, month=case.month,
                transactions=len(case.txn_ids), detected_paise=detected, next_state="DISCOVERED",
                reason=f"{len(case.txn_ids)} transaction(s) do not reconcile: {case.pattern.replace('_', ' ')}",
                evidence=[f"{f.txn_id}:{f.component}" for f in findings[:5]],
                decision="open case for investigation", action="queued for Investigation Agent",
            )
            opened.append(case)
        return opened
