"""Closed vocabularies shared across the system.

Instrument is deliberately fine-grained. Collapsing the UPI variants into a
single "UPI" value would make the nil-MDR rules unprovable, because the
statutory treatment of UPI P2M, RuPay-credit-on-UPI and PPI-on-UPI genuinely
differ. See docs/FEE_RULES.md.
"""

from __future__ import annotations

from enum import StrEnum


class Instrument(StrEnum):
    UPI_P2M_BANK = "UPI_P2M_BANK"        # bank account to merchant
    UPI_LITE = "UPI_LITE"
    RUPAY_CC_ON_UPI = "RUPAY_CC_ON_UPI"  # MDR above Rs 2,000, MCC-driven
    PPI_ON_UPI = "PPI_ON_UPI"            # wallet on UPI; merchant-facing treatment unclear
    RUPAY_DEBIT = "RUPAY_DEBIT"          # protected at ALL amounts
    CARD_DEBIT = "CARD_DEBIT"
    CARD_CREDIT = "CARD_CREDIT"
    NETBANKING = "NETBANKING"


class MerchantClass(StrEnum):
    """NPCI merchant classification. Derived state, not static configuration."""

    P2M = "P2M"
    P2PM = "P2PM"  # small merchant, <= Rs 1 lakh/month inward UPI


class RoundingPolicy(StrEnum):
    HALF_UP = "HALF_UP"
    HALF_EVEN = "HALF_EVEN"
    TRUNCATE = "TRUNCATE"


class RuleType(StrEnum):
    MDR = "MDR"
    MDR_CAP = "MDR_CAP"
    GST = "GST"
    GST_EXEMPTION = "GST_EXEMPTION"
    TCS = "TCS"
    TDS = "TDS"
    NIL_PROTECTION = "NIL_PROTECTION"
    ROUNDING = "ROUNDING"
    MATERIALITY = "MATERIALITY"
    SETTLEMENT_SLA = "SETTLEMENT_SLA"
    CLASSIFICATION = "CLASSIFICATION"


class CalculationKind(StrEnum):
    NIL = "NIL"
    FLAT_PERCENT = "FLAT_PERCENT"
    PERCENT_WITH_CAP = "PERCENT_WITH_CAP"
    FLAT_FEE = "FLAT_FEE"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class VerificationStatus(StrEnum):
    PRIMARY = "PRIMARY"      # issuer or regulator document read directly
    SECONDARY = "SECONDARY"  # reputable press reporting a circular
    ASSUMED = "ASSUMED"      # our inference; may never support an auto-filed claim


class DiscrepancyType(StrEnum):
    L1_WRONG_MDR_BAND = "L1_WRONG_MDR_BAND"
    L2_NIL_MDR_VIOLATION = "L2_NIL_MDR_VIOLATION"
    L3_GST_BASE_ERROR = "L3_GST_BASE_ERROR"
    L4_TAX_MISAPPLICATION = "L4_TAX_MISAPPLICATION"
    L5_ORPHAN_REFUND = "L5_ORPHAN_REFUND"
    L6_UNSETTLED_TRANSACTION = "L6_UNSETTLED_TRANSACTION"
    L7_DEVICE_RENTAL = "L7_DEVICE_RENTAL"


class Verdict(StrEnum):
    """The only three conclusions the Proof Engine may reach."""

    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"  # insufficient or ambiguous -> escalate, never file
    NOT_A_DISCREPANCY = "NOT_A_DISCREPANCY"


class CaseState(StrEnum):
    DISCOVERED = "DISCOVERED"
    INVESTIGATING = "INVESTIGATING"
    CANDIDATE = "CANDIDATE"
    PROVING = "PROVING"
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"
    NO_DISCREPANCY = "NO_DISCREPANCY"
    AWAITING_CYCLE = "AWAITING_CYCLE"
    BATCHED = "BATCHED"
    ACTION_PENDING = "ACTION_PENDING"
    FILED = "FILED"
    WAITING = "WAITING"
    FOLLOW_UP = "FOLLOW_UP"
    REJECTED = "REJECTED"
    REPRESENT = "REPRESENT"
    ESCALATED = "ESCALATED"
    PARTIALLY_RECOVERED = "PARTIALLY_RECOVERED"
    RECOVERED = "RECOVERED"
    CLOSED_UNRECOVERED = "CLOSED_UNRECOVERED"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"


class Actor(StrEnum):
    """Who caused an event. Exactly three agents, plus deterministic modules."""

    MONITOR_AGENT = "MONITOR_AGENT"
    INVESTIGATION_AGENT = "INVESTIGATION_AGENT"
    FOLLOWUP_AGENT = "FOLLOWUP_AGENT"
    PROOF_ENGINE = "PROOF_ENGINE"
    RULE_ENGINE = "RULE_ENGINE"
    FEE_ENGINE = "FEE_ENGINE"
    RECONCILIATION_ENGINE = "RECONCILIATION_ENGINE"
    WORKFLOW_ENGINE = "WORKFLOW_ENGINE"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
