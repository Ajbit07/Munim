"""Claims, and the counterparty that adjudicates them.

The MockClaimsDesk stands in for the payment aggregator's dispute operations.
Its behaviour is deterministic per (seed, claim, attempt), and deliberately
not a pushover:

  - it takes days to respond, and sometimes does not respond until chased
  - it rejects contract-rate claims that arrive without the signed agreement,
    and MCC-band claims without the merchant's registered MCC
  - it rejects TCS/TDS claims first, pointing at tax already deposited
  - it refuses claims on transactions older than its dispute window
  - it occasionally approves only part of a claim

That behaviour is what the Follow-up Agent must work through, and what the
memory store learns from.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any


class ClaimStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    REJECTED = "REJECTED"
    FINAL_REJECTED = "FINAL_REJECTED"
    WITHDRAWN = "WITHDRAWN"


BASE_ATTACHMENTS = frozenset({"proof", "settlement_trace", "rule_citation"})

# What each rejection needs in order to be overturned. None means it cannot be.
REMEDIES: dict[str, str | None] = {
    "RATE_AS_PER_RATE_CARD": "agreement",
    "MCC_AS_CONFIGURED": "kyc_mcc",
    "TAX_ALREADY_DEPOSITED": "eco_status_evidence",
    "OUTSIDE_DISPUTE_WINDOW": None,
}


@dataclass
class ClaimResponse:
    at: str
    status: ClaimStatus
    reason_code: str | None
    message: str
    approved_paise: int = 0


@dataclass
class Claim:
    claim_id: str
    case_id: str
    merchant_id: str
    proof_id: str
    discrepancy_type: str
    pattern: str
    amount_paise: int
    oldest_capture: date
    attachments: set[str]
    evidence: dict[str, Any]
    status: ClaimStatus = ClaimStatus.DRAFT
    workflow: str = "local"
    reference: str | None = None
    attempts: int = 0
    responses: list[ClaimResponse] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    recovered_paise: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id, "case_id": self.case_id, "reference": self.reference,
            "status": str(self.status), "workflow": self.workflow, "amount_paise": self.amount_paise,
            "attachments": sorted(self.attachments), "attempts": self.attempts,
            "recovered_paise": self.recovered_paise,
            "responses": [r.__dict__ | {"status": str(r.status)} for r in self.responses],
            "steps": self.steps,
        }


@dataclass
class _DeskEntry:
    claim: Claim
    attempt: int
    received_at: datetime
    respond_at: datetime | None
    silent: bool


class MockClaimsDesk:
    DISPUTE_WINDOW_DAYS = 180

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._entries: dict[str, _DeskEntry] = {}
        self._refs = 0

    def _rng(self, claim_id: str, attempt: int, salt: str = "") -> random.Random:
        digest = hashlib.sha256(f"{self.seed}/{claim_id}/{attempt}/{salt}".encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def receive(self, claim: Claim, now: datetime) -> str:
        rng = self._rng(claim.claim_id, claim.attempts)
        silent = claim.attempts == 1 and claim.discrepancy_type in ("L2_NIL_MDR_VIOLATION", "L6_UNSETTLED_TRANSACTION") \
            and rng.random() < 0.35
        latency = timedelta(days=rng.randint(2, 5), hours=rng.randint(0, 8))
        self._entries[claim.claim_id] = _DeskEntry(claim, claim.attempts, now, None if silent else now + latency, silent)
        if claim.reference is None:
            self._refs += 1
            claim.reference = f"DSP-{now:%Y%m%d}-{self._refs:05d}"
        return claim.reference

    def withdraw(self, claim_id: str) -> None:
        self._entries.pop(claim_id, None)

    def chase(self, claim_id: str, now: datetime) -> None:
        entry = self._entries[claim_id]
        if entry.respond_at is None:
            rng = self._rng(claim_id, entry.attempt, "chase")
            entry.respond_at = now + timedelta(days=rng.randint(1, 3))

    def check(self, claim_id: str, now: datetime) -> ClaimResponse | None:
        entry = self._entries.get(claim_id)
        if entry is None or entry.respond_at is None or now < entry.respond_at:
            return None
        del self._entries[claim_id]
        return self._decide(entry.claim, entry.attempt, entry.respond_at)

    def _decide(self, claim: Claim, attempt: int, at: datetime) -> ClaimResponse:
        rng = self._rng(claim.claim_id, attempt, "decide")
        stamp = at.isoformat()
        age = (at.date() - claim.oldest_capture).days
        if age > self.DISPUTE_WINDOW_DAYS and rng.random() < 0.5:
            return ClaimResponse(stamp, ClaimStatus.FINAL_REJECTED, "OUTSIDE_DISPUTE_WINDOW",
                                 f"Transactions older than {self.DISPUTE_WINDOW_DAYS} days fall outside the dispute window.")
        if claim.pattern == "mdr_above_agreement" and "agreement" not in claim.attachments:
            return ClaimResponse(stamp, ClaimStatus.REJECTED, "RATE_AS_PER_RATE_CARD",
                                 "Charges have been applied as per the rate card configured for the merchant.")
        if claim.pattern == "mdr_above_mcc_rate" and "kyc_mcc" not in claim.attachments:
            return ClaimResponse(stamp, ClaimStatus.REJECTED, "MCC_AS_CONFIGURED",
                                 "MDR was computed on the merchant category configured on the account.")
        if claim.pattern == "tax_on_non_eco_flow" and "eco_status_evidence" not in claim.attachments:
            return ClaimResponse(stamp, ClaimStatus.REJECTED, "TAX_ALREADY_DEPOSITED",
                                 "TCS/TDS collected has already been deposited with the government.")
        if claim.discrepancy_type == "L3_GST_BASE_ERROR" and rng.random() < 0.2:
            approved = claim.amount_paise * 9 // 10
            return ClaimResponse(stamp, ClaimStatus.PARTIALLY_APPROVED, "PARTIAL_GST_REVERSAL",
                                 "GST reversal approved for invoices within the current return period.", approved)
        return ClaimResponse(stamp, ClaimStatus.APPROVED, None,
                             "Discrepancy verified. Reversal credit will reflect in the next settlement.",
                             claim.amount_paise)
