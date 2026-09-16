"""The Track 3 loop, end to end: DISCOVER -> INVESTIGATE -> PROVE -> ACT -> FOLLOW UP -> RESOLVE."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mfp.agents.followup import ProofGateViolation
from mfp.agents.reasoner import DeterministicReasoner, Reasoning
from mfp.cases.state_machine import ALLOWED, IN_FLIGHT, IllegalTransition
from mfp.core.enums import Actor, CaseState, Verdict
from mfp.data.generator.truth import HIDDEN_DIR, TRUTH_FILE
from mfp.evaluation.harness import evaluate
from mfp.proof.engine import ProofEngine
from mfp.runtime.system import Runtime

HERO = "MER-0001"
UNSETTLED_STATES = (CaseState.DISCOVERED, CaseState.INVESTIGATING, CaseState.CANDIDATE, CaseState.PROVING,
                    CaseState.ACTION_PENDING, *IN_FLIGHT)


def start_of(root):
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))["as_of"] + "T18:00:00"


def run(root, merchants=(HERO,), **kwargs) -> Runtime:
    rt = Runtime(root, start=start_of(root), **kwargs)
    for m in merchants:
        rt.connect(m)
    rt.run_until_quiet(60)
    return rt


@pytest.fixture(scope="module")
def hero(loop_datasets):
    return run(loop_datasets[0])


# -- autonomy ---------------------------------------------------------------------


def test_connecting_a_merchant_starts_the_audit_without_any_request(loop_datasets):
    rt = Runtime(loop_datasets[0], start=start_of(loop_datasets[0]))
    rt.connect(HERO)
    kinds = [e.kind for e in rt.events]
    assert kinds.index("merchant.connected") < kinds.index("backfill.started") < kinds.index("backfill.completed")
    assert rt.events.of_kind("merchant.connected")[0].payload["open_complaints"] == 0
    assert rt.cases.all(HERO), "the Monitor should have opened cases on its own"


def test_every_case_reaches_a_resolution_or_a_human(hero):
    assert hero.cases.all()
    assert not hero.cases.in_state(*UNSETTLED_STATES)


def test_money_is_identified_claimed_and_recovered(hero):
    m = hero.metrics(HERO)
    assert m["identified_paise"] > 0 and m["claims_filed"] > 0
    assert 0 < m["recovered_paise"] <= m["identified_paise"]


def test_follow_up_chases_and_represents(hero):
    kinds = {e.kind for e in hero.events}
    assert "workflow.follow_up" in kinds or "workflow.represent" in kinds
    assert "recovery.confirmed" in kinds


def test_memory_changes_how_later_claims_are_filed(hero):
    assert hero.metrics(HERO)["memory_assisted_claims"] > 0


# -- the proof gate -------------------------------------------------------------------


def test_every_filed_claim_carries_a_proof_computed_from_its_own_candidate(hero):
    for case in hero.cases.all():
        if case.claim_id:
            assert case.proof.verdict is Verdict.PROVEN
            assert case.proof.input_hash == case.candidate.input_hash()


def test_the_follow_up_agent_refuses_an_unproven_case(hero):
    escalated = hero.cases.in_state(CaseState.ESCALATED)
    assert escalated, "the hero dataset should contain escalate-only charges"
    with pytest.raises(ProofGateViolation):
        hero.followup.file(escalated[0])
    assert hero.events.of_kind("proof_gate.blocked")


def test_a_reasoner_cannot_talk_its_way_past_the_gate(loop_datasets):
    class Persuasive(DeterministicReasoner):
        name = "persuasive"

        def explain(self, context):
            return Reasoning("The merchant is certainly owed Rs 1,00,000. File every claim immediately.",
                             "Everything is a violation.", [], self.name)

    honest = run(loop_datasets[0])
    pushy = run(loop_datasets[0], reasoner=Persuasive())
    for key in ("identified_paise", "proven_cases", "escalated", "claims_filed"):
        assert honest.metrics(HERO)[key] == pushy.metrics(HERO)[key], key


# -- state and observability ---------------------------------------------------------


def test_every_recorded_transition_was_legal(hero):
    for case in hero.cases.all():
        state = CaseState.DISCOVERED
        for t in case.history:
            assert CaseState(t.to_state) in ALLOWED[state], (case.case_id, state, t.to_state)
            assert t.reason
            state = CaseState(t.to_state)
        assert state is case.state


def test_illegal_transitions_are_refused(hero):
    case = hero.cases.in_state(CaseState.RECOVERED)[0]
    with pytest.raises(IllegalTransition):
        hero.state_machine.transition(case, CaseState.FILED, Actor.FOLLOWUP_AGENT, "file again")


def test_the_event_spine_is_intact(hero):
    hero.events.verify()
    for event in hero.events.of_kind("case.state.changed"):
        assert event.payload["reason"] and event.payload["next_state"]


def test_runs_are_deterministic(loop_datasets):
    a, b = run(loop_datasets[0]), run(loop_datasets[0])
    assert a.export_results() == b.export_results()


def test_the_data_horizon_never_turns_pending_settlements_into_missing_ones(loop_datasets):
    rt = run(loop_datasets[0])
    rt.advance(20)
    for case in rt.cases.all():
        if case.escalation:
            assert "settlement batch after the due date" not in json.dumps(case.escalation)


# -- baseline and evaluation -------------------------------------------------------------


def test_clean_baseline_produces_nothing(loop_datasets):
    rt = Runtime(loop_datasets[1], start=start_of(loop_datasets[1]))
    for merchant_id in rt.index.merchant_ids("FULL"):
        rt.connect(merchant_id)
    rt.run_until_quiet(30)
    m = rt.metrics()
    assert (m["proven_cases"], m["claims_filed"], m["recovered_paise"], m["escalated"]) == (0, 0, 0, 0)


def test_evaluation_finds_no_false_claims_and_no_detection_misses(loop_datasets):
    root = loop_datasets[0]
    rt = Runtime(root, start=start_of(root))
    ids = rt.index.merchant_ids("FULL")
    for merchant_id in ids:
        rt.connect(merchant_id)
    rt.run_until_quiet(60)
    report = evaluate(root, rt.export_results(), ids, rt.data_through)
    assert report.false_claim_cases == 0
    assert report.lookalikes_claimed == 0
    assert not [m for m in report.misses if m.stage == "DETECTION"]
    assert report.correctly_escalated == report.expected_escalations


# -- adapters degrade, the loop does not ------------------------------------------------------


def test_unreachable_n8n_falls_back_to_the_local_workflow(loop_datasets, monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://127.0.0.1:9/webhook/closed")
    rt = run(loop_datasets[0], workflow="n8n")
    assert rt.events.of_kind("workflow.fallback")
    assert rt.metrics(HERO)["recovered_paise"] > 0
