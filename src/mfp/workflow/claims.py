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

An approval is only a promise. The settlement system then pays the reversal as
an ADJUSTMENT credit in a later settlement, with its own batch and bank UTR
(SettlementAdjustments). Most arrive within two banking days; a few get stuck
until someone chases them. The Follow-up Agent counts money as recovered only
when the credit has landed, reading it the way a merchant reads a bank
statement: by reference and amount, never from the desk's internal state.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from mfp.reconciliation.calendar import add_banking_days

PAYOUT_STUCK_PROBABILITY = 0.05


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


@dataclass(frozen=True)
class AdjustmentCredit:
    """A reversal paid into the merchant's account: one settlement line, one batch, one bank credit."""

    credit_id: str
    merchant_id: str
    claim_id: str
    reference: str           # the correction reference, carried in the narration
    amount_paise: int
    settlement_date: date
    batch_id: str
    utr: str

    @property
    def narration(self) -> str:
        return f"ADJUSTMENT CR {self.reference}"

    def summary(self) -> dict[str, Any]:
        return {"credit_id": self.credit_id, "reference": self.reference, "amount_paise": self.amount_paise,
                "settlement_date": self.settlement_date.isoformat(), "batch_id": self.batch_id, "utr": self.utr,
                "narration": self.narration}


class SettlementAdjustments:
    """Reversal credits as they reach merchants' bank accounts."""

    def __init__(self) -> None:
        self._credits: list[AdjustmentCredit] = []

    def pay(self, credit: AdjustmentCredit) -> None:
        self._credits.append(credit)

    def landed(self, merchant_id: str, through: date) -> list[AdjustmentCredit]:
        return [c for c in self._credits if c.merchant_id == merchant_id and c.settlement_date <= through]

    def find(self, merchant_id: str, reference: str, through: date) -> AdjustmentCredit | None:
        return next((c for c in self.landed(merchant_id, through) if c.reference == reference), None)


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
        self.adjustments = SettlementAdjustments()
        self._stuck: dict[str, tuple[Claim, int]] = {}   # approved but not paid until chased

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
        response = self._decide(entry.claim, entry.attempt, entry.respond_at)
        if response.status in (ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_APPROVED):
            self._schedule_payout(entry.claim, response.approved_paise, entry.respond_at)
        return response

    # -- paying approved reversals ------------------------------------------------------------

    def _schedule_payout(self, claim: Claim, amount: int, approved_at: datetime) -> None:
        rng = self._rng(claim.claim_id, claim.attempts, "payout")
        if rng.random() < PAYOUT_STUCK_PROBABILITY:
            self._stuck[claim.claim_id] = (claim, amount)
            return
        self._pay(claim, amount, add_banking_days(approved_at.date(), rng.randint(1, 2)))

    def _pay(self, claim: Claim, amount: int, on: date) -> None:
        digest = hashlib.sha256(f"{self.seed}/{claim.claim_id}/{claim.reference}".encode()).hexdigest()
        self.adjustments.pay(AdjustmentCredit(
            credit_id=f"CR-ADJ-{claim.claim_id}", merchant_id=claim.merchant_id, claim_id=claim.claim_id,
            reference=claim.reference or claim.claim_id, amount_paise=amount, settlement_date=on,
            batch_id=f"ADJ-{claim.merchant_id}-{on:%Y%m%d}-{claim.claim_id[-5:]}",
            utr=f"PYTMA{int(digest[:12], 16) % 10**12:012d}"))

    def chase_payout(self, claim_id: str, now: datetime) -> None:
        """A chased payout that was stuck is released for the next banking day."""
        if claim_id in self._stuck:
            claim, amount = self._stuck.pop(claim_id)
            self._pay(claim, amount, add_banking_days(now.date(), 1))

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
