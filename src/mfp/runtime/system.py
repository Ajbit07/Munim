"""The runtime: one merchant-protection system, wired.

Owns the virtual clock, the event spine, the deterministic modules, the three
agents and their adapters, and drives the loop:

    connect(merchant)   Monitor launches the historical audit unprompted
    work()              Investigation proves, Follow-up files, until quiet
    advance(days)       time passes; Monitor watches new settlements,
                        Follow-up checks, chases, represents, recovers

Adapters are chosen by environment, each with a local fallback:
    MFP_WORKFLOW = local | n8n        MFP_MEMORY  = local | cognee
    SARVAM_API_KEY set -> Sarvam notifier, else templated
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mfp.agents.followup import FollowUpAgent
from mfp.agents.investigation import InvestigationAgent
from mfp.agents.monitor import MonitorAgent
from mfp.agents.reasoner import DeterministicReasoner, Reasoner
from mfp.cases.state_machine import IN_FLIGHT, Case, CaseRepository, CaseStateMachine
from mfp.core.clock import VirtualClock
from mfp.core.enums import Actor, CaseState, Instrument, MerchantClass
from mfp.core.events import EventLog
from mfp.data.store import MerchantIndex, MerchantView, ObservedDataset
from mfp.fees.engine import FeeEngine
from mfp.memory.store import LocalMemoryStore, MemoryStore
from mfp.network.engine import NetworkPatternEngine, SignatureEmitter
from mfp.notify.notifier import MerchantMessage, Notifier, SarvamNotifier, TemplatedNotifier
from mfp.prevention.root_cause import RootCause, RootCauseCalculator
from mfp.proof.engine import ProofEngine
from mfp.reconciliation.engine import ReconciliationEngine
from mfp.rules.engine import RuleEngine
from mfp.rules.loader import DEFAULT_CONFIG_DIR, load_raw
from mfp.workflow.claims import MockClaimsDesk
from mfp.workflow.engine import LocalWorkflowEngine, N8nWorkflowEngine

S = CaseState
RECOVERY_STATES = (S.RECOVERED, S.PARTIALLY_RECOVERED)


class Runtime:
    def __init__(
        self,
        dataset_root: Path | str,
        *,
        start: datetime | str,
        config_dir: Path | None = None,
        seed: int = 42,
        workflow: str | None = None,
        memory: MemoryStore | None = None,
        reasoner: Reasoner | None = None,
        notifier: Notifier | None = None,
        event_sink: Path | None = None,
    ) -> None:
        config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.clock = VirtualClock(start)
        self.events = EventLog(self.clock, event_sink)
        self.dataset = ObservedDataset(Path(dataset_root))
        self.index = MerchantIndex(self.dataset)
        # The settlement feed ends here. Past this date nothing new is observable,
        # so an absent settlement is "not yet received", never "missing".
        self.data_through = date.fromisoformat(self.dataset.manifest["as_of"])

        self.rules = RuleEngine.from_config(config_dir)
        self.fees = FeeEngine(self.rules)
        classification = load_raw("regulatory_rules.json", config_dir)["merchant_classification"]
        self._class_limit = classification["monthly_inward_limit_paise"]
        self._class_months = classification["transition_months"]
        self._class_from = date.fromisoformat(classification["effective_from"])
        self._inflow_cache: dict[str, dict[tuple[int, int], int]] = {}
        self.fees.classifier = self.classify
        self.recon = ReconciliationEngine(self.fees, load_raw("settlement_rules.json", config_dir))
        self.proof = ProofEngine(self.fees, self.recon, self.clock)
        self.state_machine = CaseStateMachine(self.events, self.clock)
        self.cases = CaseRepository()

        self.desk = MockClaimsDesk(seed)
        self.adjustments = self.desk.adjustments   # reversal credits as they reach merchants' accounts
        local = LocalWorkflowEngine(self.desk, self.clock, self.events)
        choice = (workflow or os.environ.get("MFP_WORKFLOW", "local")).lower()
        self.workflow = N8nWorkflowEngine(local) if choice == "n8n" else local

        if memory is None:
            if os.environ.get("MFP_MEMORY", "local").lower() == "cognee":
                from mfp.memory.cognee_store import CogneeMemoryStore
                memory = CogneeMemoryStore()
            else:
                memory = LocalMemoryStore()
        self.memory = memory

        self.reasoner = reasoner or DeterministicReasoner()
        self.notifier = notifier or (SarvamNotifier() if os.environ.get("SARVAM_API_KEY") else TemplatedNotifier())

        network_cfg = load_raw("network.json", config_dir)
        self.emitter = SignatureEmitter(network_cfg["emitter_salt"])
        self.network = NetworkPatternEngine(network_cfg["k_anonymity"])
        self.network_external = self.network.ingest(self.dataset.network_signatures)
        self.root_cause_calc = RootCauseCalculator(self.rules)
        self._root_causes: dict[str, list[RootCause]] = {}
        self._prevention_status: dict[str, str] = {}
        self.sla_breaches: dict[str, list] = {}

        self.monitor = MonitorAgent(self)
        self.investigation = InvestigationAgent(self)
        self.followup = FollowUpAgent(self)
        self.connected: list[str] = []
        self._views: dict[str, MerchantView] = {}
        self.events.append(Actor.SYSTEM, "system.started", workflow=self.workflow.name, memory=self.memory.name,
                           reasoner=self.reasoner.name, notifier=self.notifier.name,
                           network_signatures_received=self.network_external, dataset=self.dataset.root.name)

    # -- data -----------------------------------------------------------------

    def observed_through(self) -> date:
        return min(self.clock.today(), self.data_through)

    def classify(self, merchant, on: date) -> MerchantClass:
        """Rolling NPCI classification: a P2PM merchant becomes P2M after three
        consecutive months above the inward-UPI limit (regulatory_rules.json).
        No reverse transition is modelled; see FEE_RULES.md section 2.1."""
        declared = MerchantClass(merchant.upi_class)
        if declared is MerchantClass.P2M or on < self._class_from:
            return declared
        inflow = self._inflow_cache.get(merchant.merchant_id)
        if inflow is None:
            inflow = defaultdict(int)
            try:
                view = self.view(merchant.merchant_id)
            except KeyError:
                return declared
            for txn in view.transactions:
                if (txn.kind == "PAYMENT" and txn.status == "SUCCESS"
                        and txn.instrument in (Instrument.UPI_P2M_BANK, Instrument.UPI_LITE)):
                    inflow[(txn.captured_at.year, txn.captured_at.month)] += txn.amount_paise
            self._inflow_cache[merchant.merchant_id] = inflow
        year, month, streak = on.year, on.month, 0
        for _ in range(24):
            month -= 1
            if month == 0:
                year, month = year - 1, 12
            if inflow.get((year, month), 0) > self._class_limit:
                streak += 1
                if streak >= self._class_months:
                    return MerchantClass.P2M
            else:
                streak = 0
        return declared

    def view(self, merchant_id: str) -> MerchantView:
        if merchant_id not in self._views:
            self._views[merchant_id] = self.index.view(merchant_id)
        return self._views[merchant_id]

    # -- the loop ---------------------------------------------------------------

    def connect(self, merchant_id: str) -> None:
        if merchant_id in self.connected:
            return
        self.connected.append(merchant_id)
        self.monitor.connect(merchant_id)
        self.work()

    def work(self) -> None:
        for _ in range(10_000):
            progressed = False
            for case in self.cases.in_state(S.DISCOVERED):
                self.investigation.investigate(case)
                progressed = True
            self._release_batched()
            for case in self.cases.in_state(S.ACTION_PENDING):
                if self.followup.file(case) is not None:
                    progressed = True
            if not progressed:
                break
        self.refresh_root_causes()

    def _release_batched(self) -> None:
        floor = self.rules.materiality.aggregate_floor_paise
        groups: dict[tuple, list[Case]] = {}
        for case in self.cases.in_state(S.BATCHED):
            groups.setdefault((case.merchant_id, case.pattern), []).append(case)
        for (merchant_id, pattern), members in groups.items():
            total = sum(c.proof.discrepancy_paise for c in members)
            if total >= floor:
                for case in members:
                    self.state_machine.transition(
                        case, S.ACTION_PENDING, Actor.INVESTIGATION_AGENT,
                        f"Batched {pattern} cases reached the Rs {floor / 100:,.0f} aggregate floor",
                        decision="claim")

    def advance(self, days: int = 1) -> None:
        for _ in range(days):
            self.clock.advance_days(1)
            self.events.append(Actor.SYSTEM, "clock.advanced", today=self.clock.today().isoformat())
            for merchant_id in self.connected:
                self.monitor.tick(merchant_id)
            self.work()
            self.followup.tick()
            self.work()

    def run_until_quiet(self, max_days: int = 90) -> int:
        for day in range(1, max_days + 1):
            self.advance(1)
            if not self.cases.in_state(*IN_FLIGHT, S.ACTION_PENDING):
                return day
        return max_days

    # -- memory, network, prevention -------------------------------------------------

    def remember(self, case: Case) -> None:
        summary = case.summary()
        summary["outcome"] = str(case.state) if case.state in (S.RECOVERED, S.PARTIALLY_RECOVERED, S.CLOSED,
                                                               S.CLOSED_UNRECOVERED, S.ESCALATED) else None
        summary["root_cause"] = case.root_cause_id
        self.memory.record_case(summary)

    def emit_signature(self, case: Case) -> None:
        m = self.view(case.merchant_id).merchant
        signature = self.emitter.from_case(case, mcc=m.registered_mcc, route=m.acquirer_id)
        if signature is not None:
            self.network.ingest([signature])

    def refresh_root_causes(self) -> None:
        # Whether a fault is still occurring is judged against the last day we have
        # records for; a clock run past the data must not make every fault look stopped.
        today = self.observed_through()
        for merchant_id in self.connected:
            found = self.root_cause_calc.analyse(self.cases.all(merchant_id), self.view(merchant_id), today)
            for rc in found:
                if rc.status != "NEEDS_HUMAN" and rc.root_cause_id in self._prevention_status:
                    rc.status = self._prevention_status[rc.root_cause_id]
                for case_id in rc.case_ids:
                    self.cases.get(case_id).root_cause_id = rc.root_cause_id
            self._root_causes[merchant_id] = found

    def _root_cause_id(self, case: Case) -> str:
        return f"RC-{case.merchant_id}-{case.pattern}-{case.instrument or 'ANY'}"

    def prevention_status(self, rc_id: str) -> str | None:
        return self._prevention_status.get(rc_id)

    def mark_recurred(self, rc_id: str) -> None:
        self._prevention_status[rc_id] = "RECURRED"

    def review(self, case_id: str, action: str, reviewer: str, note: str = "") -> Case:
        """A person acts on a case in the human queue."""
        case = self.cases.get(case_id)
        if case.state is not S.ESCALATED:
            raise ValueError(f"{case_id} is {case.state}; only cases waiting for review can be acted on")
        if action == "file":
            case.human_attestation = {"reviewer": reviewer, "note": note, "at": self.clock.now().isoformat()}
            self.state_machine.transition(
                case, S.ACTION_PENDING, Actor.HUMAN,
                f"{reviewer} reviewed the evidence and authorised a claim"
                + (f": {note}" if note else ""), decision="claim on human authority",
                action="handed to Follow-up Agent")
            self.work()
        elif action == "dismiss":
            self.state_machine.transition(
                case, S.CLOSED, Actor.HUMAN, f"{reviewer} dismissed the case" + (f": {note}" if note else ""),
                decision="do not claim")
            self.remember(case)
        else:
            raise ValueError(f"unknown review action {action!r}")
        return case

    def request_prevention(self, case: Case) -> None:
        rc_id = self._root_cause_id(case)
        if rc_id not in self._prevention_status:
            self._prevention_status[rc_id] = "REQUESTED"
            self.events.append(Actor.FOLLOWUP_AGENT, "prevention.requested", case_id=case.case_id,
                               merchant_id=case.merchant_id, root_cause_id=rc_id,
                               reason="Correction requested alongside the first claim so the fault stops recurring")

    def confirm_prevention(self, case: Case) -> None:
        rc_id = self._root_cause_id(case)
        if self._prevention_status.get(rc_id) not in ("APPLIED",):
            self._prevention_status[rc_id] = "APPLIED"
            self.events.append(Actor.FOLLOWUP_AGENT, "prevention.applied", case_id=case.case_id,
                               merchant_id=case.merchant_id, root_cause_id=rc_id,
                               reason="Claims desk accepted the discrepancy; configuration correction confirmed")

    def root_causes(self, merchant_id: str | None = None) -> list[RootCause]:
        if merchant_id is not None:
            return self._root_causes.get(merchant_id, [])
        return [rc for rcs in self._root_causes.values() for rc in rcs]

    # -- reporting ---------------------------------------------------------------------

    def metrics(self, merchant_id: str | None = None) -> dict[str, Any]:
        cases = self.cases.all(merchant_id)
        proven = [c for c in cases if c.proof is not None and c.proof.authorises_claim]
        breaches = [b for mid, bs in self.sla_breaches.items() if merchant_id in (None, mid) for b in bs]
        human_filed = [c for c in cases if c.human_attestation and c.claim_id]
        recovered = sum(c.recovered_paise for c in cases)
        closed_unrecovered = sum(c.proven_paise for c in cases if c.state is S.CLOSED_UNRECOVERED)
        in_progress = sum(c.proven_paise for c in proven if c.state not in (*RECOVERY_STATES, S.CLOSED, S.CLOSED_UNRECOVERED))
        rcs = self.root_causes(merchant_id)
        states = Counter(str(c.state) for c in cases)
        view_ids = [merchant_id] if merchant_id else self.connected
        return {
            "as_of": self.clock.today().isoformat(),
            "merchants": len(view_ids),
            "open_complaints": 0,
            "identified_paise": sum(c.proven_paise for c in proven),
            "recovered_paise": recovered,
            "in_progress_paise": in_progress,
            "closed_unrecovered_paise": closed_unrecovered,
            "future_leakage_prevented_paise": sum(rc.future_leakage_prevented_paise for rc in rcs),
            "projected_leakage_paise": sum(rc.projected_leakage_paise for rc in rcs),
            "cases_total": len(cases),
            "proven_cases": len(proven),
            "active_claims": sum(1 for c in cases if c.state in IN_FLIGHT),
            "escalated": states.get("ESCALATED", 0),
            "escalated_paise": sum((c.escalation or {}).get("disputed_paise", 0) for c in cases if c.state is S.ESCALATED),
            "closed_no_discrepancy": sum(1 for c in cases if c.proof and not c.proof.authorises_claim and c.state is S.CLOSED),
            "claims_filed": len([c for c in cases if c.claim_id]),
            "false_claims_blocked": len(self.events.of_kind("proof_gate.blocked")),
            "states": dict(sorted(states.items())),
            "months_affected": len({c.month for c in proven}),
            "withdrawn": states.get("WITHDRAWN", 0),
            "human_filed_claims": len(human_filed),
            "human_filed_paise": sum(c.claimed_paise for c in human_filed),
            "late_settlements": len(breaches),
            "late_settlement_paise": sum(b.net_paise for b in breaches),
            "late_settlement_worst_days": max((b.banking_days_late for b in breaches), default=0),
            "regressions": sum(1 for c in cases if c.regression),
            "memory_assisted_claims": sum(1 for e in self.events.of_kind("case.state.changed")
                                          if e.payload.get("memory_applied") and (merchant_id is None or e.merchant_id == merchant_id)),
            "network": {"signatures": len(self.network), "patterns": len(self.network.patterns()[0])},
        }

    def notification(self, merchant_id: str, period: str = "Is mahine") -> MerchantMessage:
        m = self.metrics(merchant_id)
        message = self.notifier.compose({
            "merchant_id": merchant_id, "period": period, "identified_paise": m["identified_paise"],
            "recovered_paise": m["recovered_paise"], "in_progress_paise": m["in_progress_paise"],
            "escalated_cases": m["escalated"],
        })
        self.events.append(Actor.FOLLOWUP_AGENT, "merchant.notified", merchant_id=merchant_id, channel=message.channel,
                           language=message.language, source=message.source, text=message.text)
        return message

    def export_results(self) -> list[dict[str, Any]]:
        """Case outcomes in a form the evaluation harness can score."""
        out = []
        for case in self.cases.all():
            filed = any(t.to_state == "FILED" for t in case.history)
            out.append({**case.summary(), "txn_ids": list(case.txn_ids), "filed": filed,
                        "human_filed": case.human_attestation is not None})
        return out

    def export_breaches(self) -> list[dict[str, Any]]:
        return [{**b.__dict__} for bs in self.sla_breaches.values() for b in bs]

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.export_results(), indent=1, default=str), encoding="utf-8")
