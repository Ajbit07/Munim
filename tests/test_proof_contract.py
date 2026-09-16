"""The proof gate contract.

ProofResult is the only object that may authorise a claim. These tests pin the
invariants that make that safe: the arithmetic must be internally consistent,
PROVEN must be evidenced, and UNPROVEN must explain itself to the human queue.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from mfp.core.enums import DiscrepancyType, Verdict
from mfp.schemas.proof import Candidate, ComputationStep, EvidenceRef, ProofResult


def _evidence():
    return (EvidenceRef(record_type="transaction", record_id="TXN-1"),)


def _proven(**overrides):
    base = dict(
        proof_id="P-1",
        case_id="C-1",
        candidate_id="CAND-1",
        merchant_id="M-1",
        discrepancy_type=DiscrepancyType.L2_NIL_MDR_VIOLATION,
        verdict=Verdict.PROVEN,
        expected_paise=0,
        actual_paise=-1_180,
        discrepancy_paise=1_180,
        rule_ids=("MDR.UPI_P2M.NIL.UPTO_2000",),
        computation=(
            ComputationStep(
                label="expected MDR",
                expression="UPI P2M, Rs 1,500, protected under s.10A notification",
                result_paise=0,
                rule_id="MDR.UPI_P2M.NIL.UPTO_2000",
            ),
        ),
        source_records=_evidence(),
        computed_at="2026-09-16T10:04:00+05:30",
        input_hash="a" * 64,
    )
    base.update(overrides)
    return ProofResult(**base)


def test_a_proven_result_authorises_a_claim():
    assert _proven().authorises_claim is True


def test_arithmetic_must_be_internally_consistent():
    with pytest.raises(ValidationError, match="must equal"):
        _proven(discrepancy_paise=9_999)


def test_proven_requires_a_positive_discrepancy():
    with pytest.raises(ValidationError, match="positive discrepancy"):
        _proven(expected_paise=0, actual_paise=0, discrepancy_paise=0)


def test_proven_requires_at_least_one_rule():
    with pytest.raises(ValidationError, match="requires at least one rule_id"):
        _proven(rule_ids=())


def test_proven_requires_source_records():
    with pytest.raises(ValidationError, match="requires source records"):
        _proven(source_records=())


def test_unproven_must_explain_itself_for_the_human_queue():
    with pytest.raises(ValidationError, match="must state why"):
        ProofResult(
            proof_id="P-2",
            case_id="C-2",
            candidate_id="CAND-2",
            merchant_id="M-1",
            discrepancy_type=DiscrepancyType.L2_NIL_MDR_VIOLATION,
            verdict=Verdict.UNPROVEN,
            expected_paise=0,
            actual_paise=0,
            discrepancy_paise=0,
            computed_at="2026-09-16T10:04:00+05:30",
            input_hash="b" * 64,
        )


def test_unproven_with_a_reason_does_not_authorise_a_claim():
    """PPI-on-UPI is the real case: charged, unexplained, must not be filed."""
    result = ProofResult(
        proof_id="P-3",
        case_id="C-3",
        candidate_id="CAND-3",
        merchant_id="M-1",
        discrepancy_type=DiscrepancyType.L2_NIL_MDR_VIOLATION,
        verdict=Verdict.UNPROVEN,
        expected_paise=0,
        actual_paise=0,
        discrepancy_paise=0,
        computed_at="2026-09-16T10:04:00+05:30",
        input_hash="c" * 64,
        unproven_reason=(
            "Charge traced to PPI-on-UPI interchange. Whether the merchant "
            "contract passes this through as MDR is not settled by any "
            "authoritative source (see MDR.PPI_ON_UPI.INTERCHANGE assumptions)."
        ),
        missing_evidence=("merchant acquirer agreement, PPI pass-through clause",),
    )
    assert result.authorises_claim is False


def test_not_a_discrepancy_does_not_authorise_a_claim():
    """The RuPay-CC-on-UPI lookalike the red team will fire at us."""
    result = ProofResult(
        proof_id="P-4",
        case_id="C-4",
        candidate_id="CAND-4",
        merchant_id="M-1",
        discrepancy_type=DiscrepancyType.L2_NIL_MDR_VIOLATION,
        verdict=Verdict.NOT_A_DISCREPANCY,
        expected_paise=-5_250,
        actual_paise=-5_250,
        discrepancy_paise=0,
        rule_ids=("MDR.RUPAY_CC_UPI.RETAIL_110",),
        source_records=_evidence(),
        computed_at="2026-09-16T10:04:00+05:30",
        input_hash="d" * 64,
    )
    assert result.authorises_claim is False


def test_proof_result_is_frozen():
    result = _proven()
    with pytest.raises(ValidationError):
        result.discrepancy_paise = 1


def test_candidate_carries_no_financial_authority():
    """The Investigation Agent's output has no amount field at all, by design."""
    assert "discrepancy_paise" not in Candidate.model_fields
    assert "expected_paise" not in Candidate.model_fields
    assert "verdict" not in Candidate.model_fields


def test_candidate_must_reference_a_transaction():
    with pytest.raises(ValidationError, match="at least one transaction"):
        Candidate(
            candidate_id="CAND-5",
            case_id="C-5",
            merchant_id="M-1",
            discrepancy_type=DiscrepancyType.L6_UNSETTLED_TRANSACTION,
            transaction_ids=(),
            as_of=date(2026, 9, 16),
            rationale="vibes",
        )


def test_candidate_input_hash_is_stable_and_order_independent():
    def make(txns):
        return Candidate(
            candidate_id="CAND-6",
            case_id="C-6",
            merchant_id="M-1",
            discrepancy_type=DiscrepancyType.L2_NIL_MDR_VIOLATION,
            transaction_ids=txns,
            as_of=date(2026, 9, 16),
            rationale="same subject, different ordering",
        )

    assert make(("T-1", "T-2")).input_hash() == make(("T-2", "T-1")).input_hash()
