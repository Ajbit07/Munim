"""Network signatures: the only thing a merchant's agent shares with the network.

A signature describes the SHAPE of a proven discrepancy -- its type, pattern,
instrument, the rule it violated, the processor route, a coarse merchant
segment, the month, and bucketed frequency and impact. It never carries a
transaction id, a merchant id, an exact amount, or a ledger row. The schema
forbids extra fields, so a caller cannot smuggle one in.

The emitter is a salted hash. The network can count distinct merchants
without learning who they are, which is what the k-anonymity floor needs.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict, field_validator

from mfp.core.enums import DiscrepancyType, Instrument

# Pattern vocabulary shared by every emitter. Adding a discrepancy detector
# means adding a pattern here, not changing the network engine.
PATTERNS = frozenset({
    "mdr_above_mcc_rate",
    "mdr_above_agreement",
    "mdr_on_protected_instrument",
    "unverified_interchange_passthrough",
    "gst_on_exempt_settlement",
    "gst_above_standard_base",
    "tax_on_non_eco_flow",
    "refund_debited_twice",
    "refund_without_refund_event",
    "payment_missing_from_settlement",
})

_SEGMENTS = {
    "5411": "grocery", "5499": "grocery", "5541": "fuel", "5542": "fuel",
    "8220": "education", "4812": "telecom", "4814": "telecom", "5812": "food",
    "5912": "health", "7230": "personal_care", "5999": "online_retail",
}

_OCCURRENCE_BUCKETS = ((10, "1-9"), (50, "10-49"), (200, "50-199"), (None, "200+"))
IMPACT_BUCKET_PAISE = 10_000  # Rs 100


def merchant_segment(mcc: str) -> str:
    return _SEGMENTS.get(mcc, "other")


def occurrences_bucket(count: int) -> str:
    for ceiling, label in _OCCURRENCE_BUCKETS:
        if ceiling is None or count < ceiling:
            return label
    raise AssertionError("unreachable")


def impact_floor(paise: int) -> int:
    return (max(paise, 0) // IMPACT_BUCKET_PAISE) * IMPACT_BUCKET_PAISE


def emitter_hash(salt: str, merchant_id: str) -> str:
    return hashlib.sha256(f"{salt}:{merchant_id}".encode("utf-8")).hexdigest()[:16]


class NetworkSignature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signature_id: str
    emitter: str
    discrepancy_type: DiscrepancyType
    pattern: str
    instrument: Instrument | None
    rule_id: str
    processor_route: str
    merchant_segment: str
    month: str
    occurrences_bucket: str
    impact_floor_paise: int

    @field_validator("pattern")
    @classmethod
    def _known_pattern(cls, v: str) -> str:
        if v not in PATTERNS:
            raise ValueError(f"unknown signature pattern: {v}")
        return v

    @field_validator("emitter")
    @classmethod
    def _hashed(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{16}", v):
            raise ValueError("emitter must be a 16-hex salted hash, never a merchant id")
        return v

    @field_validator("month")
    @classmethod
    def _month(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}", v):
            raise ValueError("month must be YYYY-MM")
        return v

    @field_validator("impact_floor_paise")
    @classmethod
    def _bucketed(cls, v: int) -> int:
        if v % IMPACT_BUCKET_PAISE:
            raise ValueError("impact must be bucketed; exact amounts never leave the merchant")
        return v

    @property
    def aggregation_key(self) -> tuple[str, str, str]:
        return (str(self.discrepancy_type), self.pattern, str(self.instrument or "-"))
