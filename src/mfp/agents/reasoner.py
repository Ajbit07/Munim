"""Reasoning for the agents: interpretation, never arithmetic.

The Investigation Agent asks a Reasoner to explain what a group of findings
most likely means and what to check. The Follow-up Agent asks it to read a
counterparty's free-text reply. Neither answer reaches the Proof Engine.

  DeterministicReasoner  templated, instant, always available, offline

The Reasoner protocol is the seam: any other implementation plugs in here,
and the proof gate makes its answer irrelevant to what is owed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

_HYPOTHESES = {
    "mdr_above_mcc_rate": "The processor is pricing RuPay credit on UPI with a merchant category other than the registered one.",
    "mdr_above_agreement": "The processor rate card for this instrument no longer matches the signed agreement.",
    "mdr_on_protected_instrument": "A nil-MDR instrument is being charged MDR, which suggests an instrument classification or rollout error.",
    "unverified_interchange_passthrough": "Wallet interchange appears to be passed through as MDR; whether the contract allows it is not established.",
    "gst_on_exempt_settlement": "GST is being levied on card settlements that fall under the payment-aggregator exemption.",
    "gst_above_standard_base": "GST appears to be computed on a base larger than the MDR itself.",
    "tax_on_non_eco_flow": "The merchant appears to be flagged as an e-commerce participant although its flow is a plain PA flow.",
    "refund_debited_twice": "A refund debit was re-sent in a later settlement run.",
    "refund_without_refund_event": "A debit is labelled as a refund but no refund event exists in the ledger.",
    "payment_missing_from_settlement": "Captured payments were left out of the settlement file.",
}

_CHECKS = {
    "mdr_above_mcc_rate": ["processor rate-card MCC history", "registered MCC from KYC", "RuPay-CC-on-UPI MCC table"],
    "mdr_above_agreement": ["signed agreement rates", "processor rate-card history", "any agreement amendment"],
    "mdr_on_protected_instrument": ["instrument on the ledger event", "nil-charge rule in force on the capture date"],
    "unverified_interchange_passthrough": ["acquirer agreement pass-through clause"],
    "gst_on_exempt_settlement": ["transaction value against the Rs 2,000 exemption ceiling", "PA regulatory status"],
    "gst_above_standard_base": ["MDR amount on each line", "GST rate applied"],
    "tax_on_non_eco_flow": ["e-commerce participant status", "TCS/TDS lines"],
    "refund_debited_twice": ["every settlement line carrying the refund id", "refund event in the ledger"],
    "refund_without_refund_event": ["ledger for a refund or chargeback event"],
    "payment_missing_from_settlement": ["every batch after the capture", "contracted settlement SLA", "bank credits"],
}

_REJECTION_KEYWORDS = (
    ("rate card", "RATE_AS_PER_RATE_CARD"),
    ("merchant category", "MCC_AS_CONFIGURED"),
    ("deposited with the government", "TAX_ALREADY_DEPOSITED"),
    ("dispute window", "OUTSIDE_DISPUTE_WINDOW"),
)


@dataclass
class Reasoning:
    rationale: str
    hypothesis: str
    checks: list[str]
    source: str
    notes: list[str] = field(default_factory=list)


class Reasoner(Protocol):
    name: str

    def explain(self, context: dict[str, Any]) -> Reasoning: ...
    def interpret_response(self, message: str) -> str | None: ...


class DeterministicReasoner:
    name = "deterministic"

    def explain(self, context: dict[str, Any]) -> Reasoning:
        pattern = context["pattern"]
        precedent = context.get("similar_cases") or []
        rationale = (
            f"{context['transactions']} {context.get('instrument') or ''} transaction(s) in {context['month']} "
            f"show a {context['component']} deduction inconsistent with {context['rule_id']}. "
            f"Detection estimate Rs {context['detected_paise'] / 100:,.2f}; the Proof Engine decides the amount."
        )
        if precedent:
            top = precedent[0]
            rationale += (f" Precedent: {top['case_id']} ({top['pattern']}, {top['month']}) ended "
                          f"{top.get('outcome') or top.get('state')}.")
        return Reasoning(rationale.replace("  ", " "), _HYPOTHESES.get(pattern, "Unclassified deduction."),
                         _CHECKS.get(pattern, []), self.name)

    def interpret_response(self, message: str) -> str | None:
        lowered = message.lower()
        for needle, code in _REJECTION_KEYWORDS:
            if needle in lowered:
                return code
        return None
