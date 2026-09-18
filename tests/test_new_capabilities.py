"""Human review, late settlements, pattern recurrence, rolling P2PM, holidays, rentals,
and a rehearsal that proves the demo runs with the network unplugged."""

from __future__ import annotations

import json
import os
import socket
from datetime import date, timedelta

import pytest

from mfp.agents.followup import FollowUpAgent
from mfp.cases.state_machine import IllegalTransition
from mfp.core.enums import Actor, CaseState, MerchantClass
from mfp.reconciliation.calendar import add_banking_days, banking_days_between, is_banking_day
from mfp.runtime.system import Runtime

HERO = "MER-0001"


def start_of(root, days_before: int = 0) -> str:
    as_of = date.fromisoformat(json.loads((root / "manifest.json").read_text(encoding="utf-8"))["as_of"])
    return f"{as_of - timedelta(days=days_before)}T18:00:00"


def run(root, **kwargs) -> Runtime:
    rt = Runtime(root, start=start_of(root), **kwargs)
    rt.connect(HERO)
    rt.run_until_quiet(60)
    return rt


@pytest.fixture()
def hero(loop_datasets):
    return run(loop_datasets[0])


# -- human review -------------------------------------------------------------------------


def test_only_a_person_can_release_an_escalated_case(hero):
    case = hero.cases.in_state(CaseState.ESCALATED)[0]
    for actor in (Actor.INVESTIGATION_AGENT, Actor.FOLLOWUP_AGENT, Actor.MONITOR_AGENT):
        with pytest.raises(IllegalTransition):
            hero.state_machine.transition(case, CaseState.ACTION_PENDING, actor, "agent tries to self-authorise")
    assert case.state is CaseState.ESCALATED


def test_filing_on_human_authority_is_attested_and_followed_through(hero):
    case = max(hero.cases.in_state(CaseState.ESCALATED), key=lambda c: (c.escalation or {}).get("disputed_paise", 0))
    hero.review(case.case_id, "file", reviewer="Ops reviewer", note="interchange confirmed with the network")
    assert case.human_attestation["reviewer"] == "Ops reviewer"
    assert case.claim_id, "the Follow-up Agent should file once a person authorises it"
    claim = hero.followup.claims[case.claim_id]
    assert "human_attestation" in claim.attachments
    assert claim.amount_paise == case.claimed_paise > 0
    assert hero.metrics(HERO)["human_filed_claims"] == 1


def test_dismissing_closes_the_case_without_a_claim(hero):
    case = hero.cases.in_state(CaseState.ESCALATED)[0]
    hero.review(case.case_id, "dismiss", reviewer="Ops reviewer", note="covered by a separate agreement")
    assert case.state is CaseState.CLOSED and case.claim_id is None


def test_review_refuses_cases_that_are_not_waiting_for_a_person(hero):
    settled = next(c for c in hero.cases.all() if c.state is not CaseState.ESCALATED)
    with pytest.raises(ValueError):
        hero.review(settled.case_id, "file", reviewer="Ops reviewer")


# -- late settlement ----------------------------------------------------------------------


def test_late_settlements_are_reported_not_claimed(hero):
    breaches = hero.sla_breaches.get(HERO, [])
    assert breaches, "the hero's delay profile should produce SLA breaches"
    assert all(b.settled_on > b.deadline and b.banking_days_late > 0 for b in breaches)
    assert hero.events.of_kind("settlement.delay.detected")
    late_txns = {b.txn_id for b in breaches}
    for case in hero.cases.all(HERO):
        if case.claim_id:
            assert not (set(case.txn_ids) & late_txns) or case.component != "SETTLEMENT"


def test_a_missing_payment_that_arrives_late_is_withdrawn(loop_datasets, monkeypatch):
    monkeypatch.setattr(FollowUpAgent, "settled_since",
                        lambda self, case: "arrived in a later batch" if case.component == "SETTLEMENT" else None)
    rt = run(loop_datasets[0])
    missing = [c for c in rt.cases.all(HERO) if c.component == "SETTLEMENT" and c.proof is not None]
    assert missing, "the loop dataset should contain missing-payment cases"
    for case in missing:
        assert case.state in (CaseState.WITHDRAWN, CaseState.ESCALATED)
    assert rt.metrics(HERO)["withdrawn"] > 0


# -- recurrence ---------------------------------------------------------------------------


def test_a_pattern_that_returns_after_its_fix_is_flagged(loop_datasets):
    root = loop_datasets[0]
    rt = Runtime(root, start=start_of(root, days_before=40))
    rt.connect(HERO)
    rt.run_until_quiet(5)
    for case in rt.cases.all(HERO):
        rt._prevention_status[f"RC-{HERO}-{case.pattern}-{case.instrument or 'ANY'}"] = "APPLIED"
    for _ in range(40):
        rt.advance(1)
    recurred = rt.events.of_kind("pattern.recurred")
    assert recurred, "an ongoing pattern marked fixed must be caught when it recurs"
    assert any(c.regression for c in rt.cases.all(HERO))
    assert rt.metrics(HERO)["regressions"] > 0


# -- rolling P2PM classification -----------------------------------------------------------


def test_p2pm_classification_follows_three_months_of_inflow(loop_datasets):
    rt = Runtime(loop_datasets[0], start=start_of(loop_datasets[0]))
    merchant = rt.dataset.merchant(HERO)
    p2m = merchant.model_copy(update={"upi_class": "P2M"})
    assert rt.classify(p2m, date(2026, 12, 1)) is MerchantClass.P2M
    p2pm = merchant.model_copy(update={"upi_class": "P2PM"})
    assert rt.classify(p2pm, date(2026, 9, 1)) is MerchantClass.P2PM  # before 15 Oct the declared class stands
    # The hero takes far more than Rs 1 lakh of UPI a month, so after 15 Oct it is P2M.
    assert rt.classify(p2pm, date(2026, 10, 20)) is MerchantClass.P2M


# -- holidays -----------------------------------------------------------------------------


def test_banking_day_arithmetic_skips_weekends_and_holidays():
    holidays = frozenset({date(2026, 10, 2)})  # Gandhi Jayanti, a Friday
    assert not is_banking_day(date(2026, 10, 2), holidays)
    assert add_banking_days(date(2026, 10, 1), 1, holidays) == date(2026, 10, 5)
    assert add_banking_days(date(2026, 10, 1), 1) == date(2026, 10, 2)
    assert banking_days_between(date(2026, 10, 1), date(2026, 10, 6), holidays) == 2
    assert banking_days_between(date(2026, 10, 6), date(2026, 10, 1), holidays) == 0


# -- rentals --------------------------------------------------------------------------------


def test_rental_debited_after_return_is_proven_from_device_records(redteam_root):
    rt = Runtime(redteam_root, start=start_of(redteam_root))
    for m in rt.dataset.merchants:
        rt.connect(m.merchant_id)
    rt.run_until_quiet(30)
    rentals = [c for c in rt.cases.all() if c.component == "RENTAL"]
    assert rentals
    proven = [c for c in rentals if c.proof is not None and c.proof.authorises_claim]
    # Only the control (returned in July, billed in September) is owed; the active month
    # and the month of return are chargeable and must not be claimed.
    assert len(proven) == 1 and proven[0].proof.discrepancy_paise == 19_900
    assert proven[0].claim_id


# -- rehearsal with the network unplugged --------------------------------------------------


def test_the_demo_rehearses_with_no_network(loop_datasets, monkeypatch):
    """Every step of the stage demo must run with nothing but localhost reachable."""
    from mfp.demo.director import DemoDirector

    real_connect = socket.socket.connect
    attempts: list = []

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(address)
            raise OSError(f"network disabled for rehearsal: {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for var in ("SARVAM_API_KEY", "N8N_WEBHOOK_URL", "MFP_WORKFLOW"):
        monkeypatch.delenv(var, raising=False)
    results = DemoDirector(data_dir=loop_datasets[0].parent, seed=5).run_all()
    assert len(results) == 12
    assert attempts == [], f"the demo tried to reach the network: {attempts}"


@pytest.mark.skipif(not os.environ.get("MFP_N8N_LIVE"), reason="set MFP_N8N_LIVE=1 with n8n and the API running")
def test_live_n8n_executes_the_claim_lifecycle():
    import httpx

    url = os.environ.get("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/mfp-claim-lifecycle")
    reply = httpx.post(url, json={"step": "submit", "claim_id": "CLM-LIVE-TEST", "case_id": "CASE-LIVE"}, timeout=10)
    assert reply.status_code == 200 and reply.json().get("executed_by") == "n8n"
