"""The proof gate contract.

ProofResult is the only thing that may authorise a claim. It is frozen, it
carries the hash of the inputs it was computed from, and it can only be built
by the Proof Engine.

The LLM may propose a Candidate. It may never construct a ProofResult.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mfp.core.enums import DiscrepancyType, Instrument, Verdict


class ComputationStep(BaseModel):
    """One line of the arithmetic, in the order a human would check it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    expression: str           # e.g. "250000 paise x 0.4% (cap 30000)"
    result_paise: int | None = None
    rule_id: str | None = None


class EvidenceRef(BaseModel):
    """A pointer to an observed record. Never a copy of merchant data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_type: str  # "transaction" | "settlement_line" | "bank_credit" | "config_snapshot"
    record_id: str
    note: str | None = None


class Candidate(BaseModel):
    """What the Investigation Agent proposes. Carries no financial authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    case_id: str
    merchant_id: str
    discrepancy_type: DiscrepancyType
    transaction_ids: tuple[str, ...]
    settlement_batch_id: str | None = None
    as_of: date
    rationale: str  # may be LLM-authored; explanatory only
    evidence: tuple[EvidenceRef, ...] = ()
    suggested_rule_ids: tuple[str, ...] = ()
    # What the detection claims to have found, so the Proof Engine can re-derive
    # exactly that component. None of these carry an amount.
    component: str | None = None        # MDR | GST | TAX | REFUND_DEBIT | SETTLEMENT | UNRESOLVED
    pattern: str | None = None
    instrument: Instrument | None = None

    @model_validator(mode="after")
    def _has_subject(self) -> Candidate:
        if not self.transaction_ids:
            raise ValueError("a candidate must reference at least one transaction")
        return self

    def input_hash(self) -> str:
        body = {
            "merchant_id": self.merchant_id,
            "discrepancy_type": str(self.discrepancy_type),
            "transaction_ids": sorted(self.transaction_ids),
            "settlement_batch_id": self.settlement_batch_id,
            "as_of": self.as_of.isoformat(),
            "component": self.component,
            "pattern": self.pattern,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProofResult(BaseModel):
    """The deterministic verdict. Immutable, traceable, and the only claim key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proof_id: str
    case_id: str
    candidate_id: str
    merchant_id: str
    discrepancy_type: DiscrepancyType

    verdict: Verdict
    expected_paise: int
    actual_paise: int
    discrepancy_paise: int = Field(
        description="expected minus actual; positive means the merchant is owed"
    )

    rule_ids: tuple[str, ...] = ()
    computation: tuple[ComputationStep, ...] = ()
    source_records: tuple[EvidenceRef, ...] = ()

    computed_at: str           # ISO-8601 from the injected Clock
    input_hash: str            # hash of the Candidate this was computed from
    engine_version: str = "1.0.0"

    # Populated when verdict is UNPROVEN, so the human queue knows what is missing.
    unproven_reason: str | None = None
    missing_evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _arithmetic_is_consistent(self) -> ProofResult:
        if self.discrepancy_paise != self.expected_paise - self.actual_paise:
            raise ValueError(
                f"{self.proof_id}: discrepancy_paise must equal "
                "expected_paise - actual_paise"
            )
        return self

    @model_validator(mode="after")
    def _verdict_is_coherent(self) -> ProofResult:
        if self.verdict is Verdict.PROVEN:
            if self.discrepancy_paise <= 0:
                raise ValueError(
                    f"{self.proof_id}: PROVEN requires a positive discrepancy"
                )
            if not self.rule_ids:
                raise ValueError(
                    f"{self.proof_id}: PROVEN requires at least one rule_id"
                )
            if not self.source_records:
                raise ValueError(
                    f"{self.proof_id}: PROVEN requires source records"
                )
        if self.verdict is Verdict.UNPROVEN and not self.unproven_reason:
            raise ValueError(
                f"{self.proof_id}: UNPROVEN must state why, for the human queue"
            )
        return self

    @property
    def authorises_claim(self) -> bool:
        """The single predicate the Follow-up Agent is allowed to consult."""
        return self.verdict is Verdict.PROVEN and self.discrepancy_paise > 0

    @property
    def discrepancy_rupees(self) -> Decimal:
        return Decimal(self.discrepancy_paise) / 100
