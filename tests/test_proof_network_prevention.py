"""Proof Engine verdicts, network privacy, prevention horizons, adapters, red team."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from mfp.agents.reasoner import DeterministicReasoner
from mfp.core.clock import VirtualClock
from mfp.core.enums import CaseState, Verdict
from mfp.data.store import MerchantIndex, MerchantView, ObservedDataset
from mfp.evaluation.redteam import run_redteam
from mfp.fees.engine import FeeEngine
from mfp.network.engine import NetworkPatternEngine
from mfp.notify.notifier import TemplatedNotifier, rupees
from mfp.proof.engine import ProofEngine
from mfp.reconciliation.engine import ReconciliationEngine
from mfp.redteam.generator import AS_OF
from mfp.rules.engine import RuleEngine
from mfp.rules.loader import load_raw
from mfp.schemas.network import NetworkSignature
from mfp.schemas.proof import Candidate


@pytest.fixture(scope="module")
def engines():
    rules = RuleEngine.from_config()
    fees = FeeEngine(rules)
    recon = ReconciliationEngine(fees, load_raw("settlement_rules.json"))
    return rules, fees, recon, ProofEngine(fees, recon, VirtualClock(f"{AS_OF}T18:00:00"))


def scenario_view(root, key) -> MerchantView:
    expectations = json.loads((root / "_hidden" / "redteam_expectations.json").read_text(encoding="utf-8"))
    merchant_id = next(e["merchant_id"] for e in expectations if e["scenario"] == key)
    return MerchantIndex(ObservedDataset(root)).view(merchant_id)


def candidate(view, component, dtype, txn_ids=None):
    txns = txn_ids or tuple(t.txn_id for t in view.transactions if t.kind == "PAYMENT")[:1]
    return Candidate(candidate_id="C", case_id="CASE", merchant_id=view.merchant_id, discrepancy_type=dtype,
                     transaction_ids=txns, as_of=AS_OF, rationale="test", component=component)


# -- proof ------------------------------------------------------------------------


def test_a_genuine_discrepancy_is_proven_with_a_traceable_chain(engines, redteam_root):
    view = scenario_view(redteam_root, "control_upi_mdr_before_oct15")
    proof = engines[3].prove(candidate(view, "MDR", "L2_NIL_MDR_VIOLATION"), view, AS_OF, "P1")
    assert proof.verdict is Verdict.PROVEN and proof.discrepancy_paise == 2_360
    kinds = {r.record_type for r in proof.source_records}
    assert {"transaction", "settlement_line", "settlement_batch", "bank_credit"} <= kinds
    assert "MDR.UPI_P2M.NIL.LEGACY" in proof.rule_ids


def test_a_charge_on_an_assumed_rule_is_unproven(engines, redteam_root):
    view = scenario_view(redteam_root, "ppi_interchange_charged")
    proof = engines[3].prove(candidate(view, "MDR", "L2_NIL_MDR_VIOLATION"), view, AS_OF, "P2")
    assert proof.verdict is Verdict.UNPROVEN and "ASSUMED" in proof.unproven_reason
    assert not proof.authorises_claim


def test_a_broken_bank_trail_blocks_the_proof(engines, redteam_root):
    view = scenario_view(redteam_root, "control_upi_mdr_before_oct15")
    broken = MerchantView(view.merchant, view.agreements, view.processor_config, view.transactions,
                          view.settlement_batches, view.settlement_lines, [])
    proof = engines[3].prove(candidate(broken, "MDR", "L2_NIL_MDR_VIOLATION"), broken, AS_OF, "P3")
    assert proof.verdict is Verdict.UNPROVEN
    assert any("bank credit" in m for m in proof.missing_evidence)


def test_a_legitimate_charge_is_not_a_discrepancy(engines, redteam_root):
    view = scenario_view(redteam_root, "rupay_cc_upi_correct_mcc_rate")
    proof = engines[3].prove(candidate(view, "MDR", "L1_WRONG_MDR_BAND"), view, AS_OF, "P4")
    assert proof.verdict is Verdict.NOT_A_DISCREPANCY


def test_a_payment_not_yet_due_is_not_unsettled(engines, redteam_root):
    view = scenario_view(redteam_root, "not_yet_due_at_as_of")
    proof = engines[3].prove(candidate(view, "SETTLEMENT", "L6_UNSETTLED_TRANSACTION"), view, AS_OF, "P5")
    assert proof.verdict is Verdict.NOT_A_DISCREPANCY


def test_a_dropped_payment_is_proven_at_its_expected_net(engines, redteam_root):
    view = scenario_view(redteam_root, "control_dropped_payment")
    dropped = (view.transactions[0].txn_id,)
    proof = engines[3].prove(candidate(view, "SETTLEMENT", "L6_UNSETTLED_TRANSACTION", dropped), view, AS_OF, "P6")
    assert proof.verdict is Verdict.PROVEN and proof.discrepancy_paise == 450_000


def test_red_team_scores_perfectly_with_controls(tmp_path):
    summary = run_redteam(tmp_path).summary()
    assert summary["false_claims"] == 0
    assert summary["correct"] == summary["generated"]
    assert summary["controls_claimed"] == summary["controls"] >= 7
    assert summary["correctly_escalated"] >= 2


# -- network privacy ---------------------------------------------------------------------


def sig(emitter_n: int, **over):
    base = dict(signature_id=f"S{emitter_n}", emitter=f"{emitter_n:016x}", discrepancy_type="L2_NIL_MDR_VIOLATION",
                pattern="mdr_on_protected_instrument", instrument="UPI_P2M_BANK", rule_id="MDR.UPI_P2M.NIL.LEGACY",
                processor_route="ACQ-B", merchant_segment="grocery", month="2026-09", occurrences_bucket="10-49",
                impact_floor_paise=20_000)
    return NetworkSignature(**{**base, **over})


def test_patterns_below_k_are_suppressed_and_reveal_nothing():
    engine = NetworkPatternEngine(k_anonymity=5)
    engine.ingest(sig(i) for i in range(4))
    patterns, suppressed = engine.patterns()
    assert patterns == [] and suppressed == 1
    engine.ingest([sig(4)])
    patterns, suppressed = engine.patterns()
    assert patterns[0].merchants_affected == 5 and suppressed == 0


def test_the_network_accepts_signatures_only():
    with pytest.raises(TypeError):
        NetworkPatternEngine().ingest([{"merchant_id": "MER-0001", "txn_id": "TXN-1"}])


@pytest.mark.parametrize("override", [
    {"emitter": "MER-0001"},
    {"impact_floor_paise": 12_345},
    {"pattern": "anything_goes"},
])
def test_signatures_reject_identity_exact_amounts_and_unknown_patterns(override):
    with pytest.raises(ValidationError):
        sig(1, **override)


def test_signatures_forbid_extra_fields():
    with pytest.raises(ValidationError):
        sig(1, txn_id="TXN-1")


# -- prevention ---------------------------------------------------------------------------


def test_early_upi_mdr_leakage_horizon_stops_at_the_regime_boundary(engines):
    from mfp.prevention.root_cause import RootCauseCalculator
    weeks, note = RootCauseCalculator(engines[0])._horizon(
        "mdr_on_protected_instrument", "UPI_P2M_BANK", {"MDR.UPI_P2M.NIL.LEGACY"}, date(2026, 9, 15), "P2M")
    assert weeks == pytest.approx(30 / 7) and "2026-10-14" in note
    weeks, _ = RootCauseCalculator(engines[0])._horizon("mdr_above_agreement", "CARD_CREDIT", set(), date(2026, 9, 15), "P2M")
    assert weeks == 26


# -- communication and adapters ---------------------------------------------------------------


def test_rupees_use_indian_digit_grouping():
    assert rupees(1_23_45_678_00) == "₹1,23,45,678"
    assert rupees(438_000) == "₹4,380"


def test_merchant_message_is_short_and_in_hinglish():
    msg = TemplatedNotifier().compose({"identified_paise": 438_000, "recovered_paise": 438_000})
    assert msg.text == "Is mahine ₹4,380 ki settlement discrepancy identify hui aur recover ho gayi."
    clean = TemplatedNotifier().compose({"identified_paise": 0, "recovered_paise": 0})
    assert "koi discrepancy nahi" in clean.text


def test_deterministic_reasoner_reads_a_rate_card_rejection():
    reasoner = DeterministicReasoner()
    assert reasoner.interpret_response("Charges have been applied as per the rate card.") == "RATE_AS_PER_RATE_CARD"
