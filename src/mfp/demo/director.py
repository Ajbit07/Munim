"""The demo, as one continuous autonomous story.

Lives in mfp.demo, the presentation layer, because it displays red-team
results from mfp.evaluation. Production packages (agents, engines, runtime)
may not import evaluation code; this layer sits above them and may.


Twelve steps, each a real operation on a real runtime. Nothing on screen is a
hardcoded number: every figure comes from generated records, agent decisions
and workflow results. The CLI (demo.py) and the command center UI drive the
same director.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mfp.cases.state_machine import TERMINAL, Case
from mfp.core.enums import Actor, CaseState
from mfp.evaluation.redteam import run_redteam
from mfp.runtime.system import Runtime
from mfp.runtime.views import case_detail

HERO = "MER-0001"
DEMO_START = "2026-09-10T09:00:00"
REPO = Path(__file__).resolve().parents[3]


@dataclass
class Step:
    key: str
    title: str
    narrative: str
    run: Callable[[], dict[str, Any]]


@dataclass
class DemoDirector:
    data_dir: Path = REPO / "data" / "generated"
    seed: int = 42
    merchant_id: str = HERO
    index: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rt = Runtime(self.data_dir / f"seed-{self.seed}", start=DEMO_START, seed=self.seed)
        self.showcase: Case | None = None
        self.redteam: dict[str, Any] | None = None
        self.baseline: dict[str, Any] | None = None
        self.steps = [
            Step("zero_complaints", "No one has complained",
                 "The merchant has not reported a problem, opened a ticket, or asked a question.", self._zero),
            Step("monitor_wakes", "The Monitor Agent notices settlement activity",
                 "Nobody prompted it. A merchant is connected and settlements are flowing.", self._wake),
            Step("backfill", "It audits twelve months before waiting for anything new",
                 "Every batch, line and bank credit is reconstructed and reconciled.", self._backfill),
            Step("investigation", "The Investigation Agent works a case",
                 "Records, rules, rate-card history and precedent are gathered before anything is concluded.", self._investigate),
            Step("proof", "The Proof Engine decides",
                 "Deterministic recomputation from published rules. The language model has no vote.", self._proof),
            Step("claim", "The Follow-up Agent files the claim",
                 "Only because the proof authorised it. The workflow executes the lifecycle.", self._claim),
            Step("follow_up", "Time passes; the agent keeps working",
                 "New batches arrive, the desk is slow, rejections come back. The agent chases and represents.", self._follow_up),
            Step("recovery", "Money comes back, and the cause is fixed",
                 "Recovered amounts are confirmed, and corrections are requested so the leak stops.", self._recovery),
            Step("network", "Zoom out: this is not one merchant's problem",
                 "Privacy-safe signatures from other merchants' agents reveal systemic patterns.", self._network),
            Step("red_team", "Try to make it file a false claim",
                 "Legitimate charges built to look like violations, plus genuine controls.", self._red_team),
            Step("baseline", "Run it where nothing is wrong",
                 "The same merchant's ledger, with no leakage planted.", self._baseline),
            Step("notify", "Tell the merchant, in one sentence",
                 "The merchant did not ask. The agent found it, proved it, acted on it and followed it through.", self._notify),
        ]

    # -- driving --------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.index >= len(self.steps)

    def outline(self) -> list[dict[str, Any]]:
        return [{"n": i + 1, "key": s.key, "title": s.title, "done": i < self.index} for i, s in enumerate(self.steps)]

    def next(self) -> dict[str, Any]:
        if self.finished:
            return {"finished": True}
        step = self.steps[self.index]
        self.rt.events.append(Actor.SYSTEM, "demo.step", step=self.index + 1, key=step.key, title=step.title)
        payload = {"n": self.index + 1, "key": step.key, "title": step.title, "narrative": step.narrative,
                   "data": step.run()}
        self.index += 1
        self.results.append(payload)
        return payload

    def run_all(self) -> list[dict[str, Any]]:
        while not self.finished:
            self.next()
        return self.results

    # -- steps ---------------------------------------------------------------------

    def _zero(self) -> dict[str, Any]:
        m = self.rt.dataset.merchant(self.merchant_id)
        return {"open_complaints": 0, "merchant": m.legal_name, "mcc": m.registered_mcc, "acquirer": m.acquirer_id,
                "today": self.rt.clock.today().isoformat()}

    def _wake(self) -> dict[str, Any]:
        view = self.rt.view(self.merchant_id)
        today = self.rt.observed_through()
        batches = [b for b in view.settlement_batches if b.settlement_date <= today]
        self.rt.events.append(Actor.MONITOR_AGENT, "settlement.activity.detected", merchant_id=self.merchant_id,
                              batches=len(batches), latest=batches[-1].settlement_date.isoformat(),
                              reason="Settlement history present and no audit on record; scheduling a historical audit")
        return {"batches_seen": len(batches), "latest_batch": batches[-1].batch_id,
                "first_settlement": batches[0].settlement_date.isoformat()}

    def _backfill(self) -> dict[str, Any]:
        self.rt.connect(self.merchant_id)
        done = self.rt.events.of_kind("backfill.completed")[-1].payload
        m = self.rt.metrics(self.merchant_id)
        return {**done, "identified_paise": m["identified_paise"], "proven_cases": m["proven_cases"],
                "months_affected": m["months_affected"], "escalated": m["escalated"], "cases_total": m["cases_total"]}

    def _pick_showcase(self) -> Case:
        proven = [c for c in self.rt.cases.all(self.merchant_id) if c.proof and c.proof.authorises_claim]
        preferred = [c for c in proven if c.pattern == "mdr_on_protected_instrument" and c.claim_id]
        pool = preferred or [c for c in proven if c.claim_id] or proven
        return max(pool, key=lambda c: (c.month, c.proof.discrepancy_paise))

    def _investigate(self) -> dict[str, Any]:
        self.showcase = self._pick_showcase()
        detail = case_detail(self.rt, self.showcase.case_id)
        return {"case": detail["case"], "rationale": detail["case"]["rationale"],
                "investigation": [t for t in detail["history"] if t["actor"] == str(Actor.INVESTIGATION_AGENT)],
                "similar_cases": detail["similar_cases"]}

    def _proof(self) -> dict[str, Any]:
        return {"proof": case_detail(self.rt, self.showcase.case_id)["proof"]}

    def _claim(self) -> dict[str, Any]:
        detail = case_detail(self.rt, self.showcase.case_id)
        return {"claim": detail["claim"], "workflow": self.rt.workflow.name}

    def _follow_up(self) -> dict[str, Any]:
        start = len(self.rt.events)
        days = 0
        while days < 21 and self.showcase.state not in TERMINAL and self.showcase.state is not CaseState.ESCALATED:
            self.rt.advance(1)
            days += 1
        new = self.rt.events.events[start:]
        kinds = [e.kind for e in new]
        return {"days_advanced": days, "today": self.rt.clock.today().isoformat(),
                "new_batches": kinds.count("settlement.batch.observed"), "follow_ups": kinds.count("workflow.follow_up"),
                "represented": kinds.count("workflow.represent"), "recoveries": kinds.count("recovery.confirmed"),
                "showcase": case_detail(self.rt, self.showcase.case_id)["case"]}

    def _recovery(self) -> dict[str, Any]:
        days = self.rt.run_until_quiet(60)
        return {"days_advanced": days, "metrics": self.rt.metrics(self.merchant_id),
                "root_causes": [rc.summary() for rc in self.rt.root_causes(self.merchant_id)]}

    def _network(self) -> dict[str, Any]:
        if os.environ.get("MFP_DEMO_FULL_NETWORK") == "1":
            for merchant_id in self.rt.index.merchant_ids("FULL"):
                self.rt.connect(merchant_id)
            self.rt.run_until_quiet(60)
        patterns, suppressed = self.rt.network.patterns()
        return {"signatures": len(self.rt.network), "patterns": [p.summary() for p in patterns],
                "suppressed_below_k": suppressed, "k": self.rt.network.k}

    def _red_team(self) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            self.redteam = run_redteam(Path(tmp)).summary()
        self.rt.events.append(Actor.SYSTEM, "redteam.completed", generated=self.redteam["generated"],
                              false_claims=self.redteam["false_claims"], correct=self.redteam["correct"])
        return self.redteam

    def _baseline(self) -> dict[str, Any]:
        rt = Runtime(self.data_dir / f"seed-{self.seed}-baseline", start=DEMO_START, seed=self.seed)
        rt.connect(self.merchant_id)
        rt.run_until_quiet(30)
        m = rt.metrics(self.merchant_id)
        done = rt.events.of_kind("backfill.completed")[-1].payload
        self.baseline = {"lines": done["lines"], "batches": done["batches"], "proven_cases": m["proven_cases"],
                         "claims_filed": m["claims_filed"], "recovered_paise": m["recovered_paise"],
                         "escalated": m["escalated"], "cases_total": m["cases_total"]}
        self.rt.events.append(Actor.SYSTEM, "baseline.completed", **self.baseline)
        return self.baseline

    def _notify(self) -> dict[str, Any]:
        message = self.rt.notification(self.merchant_id)
        return {"text": message.text, "english": message.english, "language": message.language,
                "channel": message.channel, "source": message.source}
