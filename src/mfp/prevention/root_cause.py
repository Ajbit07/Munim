"""Root cause, prevention and future leakage.

Recovering money fixes the symptom. For every family of proven cases -- same
merchant, same pattern, same instrument -- this module states:

  ROOT CAUSE          why it happened, citing the rate-card change or first
                      occurrence that the evidence shows
  HISTORICAL IMPACT   proven amount across the family
  RECOVERED           amount actually returned so far
  WEEKLY LEAKAGE      proven amount over the trailing 8 weeks / 8, if the fault
                      is still occurring; zero if it has stopped
  FUTURE LEAKAGE      weekly leakage x prevention horizon, counted as
                      PREVENTED only once the correction is acknowledged

Methodology, stated so it can be challenged:
  - the horizon is 26 weeks, the period over which an uncorrected rate-card
    or classification fault would plausibly persist
  - it is CAPPED at any regulatory boundary where the charge becomes
    legitimate. An early-applied 0.4% UPI MDR stops being leakage for P2M
    transactions above Rs 2,000 on 15 Oct 2026, so its horizon ends 14 Oct.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from mfp.cases.state_machine import Case
from mfp.core.enums import CaseState, Instrument
from mfp.data.store import MerchantView
from mfp.rules.engine import RuleEngine

HORIZON_WEEKS = 26
TRAILING_WEEKS = 8
ACTIVE_WITHIN_DAYS = 21

_ACTIONS = {
    "mdr_above_mcc_rate": "Correct the processor pricing MCC to the registered MCC",
    "mdr_above_agreement": "Restore the processor rate card to the signed agreement rate",
    "gst_on_exempt_settlement": "Apply the Sl. 34 GST exemption to card settlements up to Rs 2,000",
    "gst_above_standard_base": "Compute GST on the MDR amount only",
    "tax_on_non_eco_flow": "Remove the e-commerce participant flag and stop TCS/TDS on this PA flow",
    "refund_debited_twice": "De-duplicate refund debits by refund id before each settlement run",
    "refund_without_refund_event": "Reject settlement debits that carry no refund or chargeback event",
    "payment_missing_from_settlement": "Reconcile batch-close captures into the next settlement file",
    "unverified_interchange_passthrough": "Obtain the acquirer's contractual basis for wallet interchange pass-through",
}


@dataclass
class RootCause:
    root_cause_id: str
    merchant_id: str
    discrepancy_type: str
    pattern: str
    instrument: str | None
    cause: str
    evidence: list[str]
    first_seen: date
    last_seen: date
    case_ids: list[str]
    historical_impact_paise: int
    recovered_paise: int
    weekly_leakage_paise: int
    horizon_weeks: float
    horizon_note: str
    projected_leakage_paise: int
    prevention_action: str
    status: str = "RECOMMENDED"            # RECOMMENDED | REQUESTED | APPLIED | NEEDS_HUMAN
    notes: list[str] = field(default_factory=list)

    @property
    def future_leakage_prevented_paise(self) -> int:
        return self.projected_leakage_paise if self.status == "APPLIED" else 0

    def summary(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["first_seen"], d["last_seen"] = self.first_seen.isoformat(), self.last_seen.isoformat()
        d["future_leakage_prevented_paise"] = self.future_leakage_prevented_paise
        return d


class RootCauseCalculator:
    def __init__(self, rules: RuleEngine) -> None:
        self.rules = rules

    def _cause(self, pattern: str, instrument: str | None, view: MerchantView, first: date) -> tuple[str, list[str], str]:
        m = view.merchant
        action = _ACTIONS.get(pattern, "Investigate the processor configuration")
        if pattern == "mdr_above_mcc_rate":
            for snap in view.processor_config:
                if snap.pricing_mcc != m.registered_mcc and snap.effective_from <= first:
                    return (f"Processor pricing MCC {snap.pricing_mcc} differs from registered MCC {m.registered_mcc} "
                            f"since {snap.effective_from}",
                            [f"config snapshot {snap.snapshot_id}", f"merchant KYC MCC {m.registered_mcc}"],
                            f"Correct pricing MCC {snap.pricing_mcc} -> {m.registered_mcc}")
        if pattern == "mdr_above_agreement":
            agreement = max(view.agreements, key=lambda a: a.signed_on)
            for snap in view.processor_config:
                if snap.card_credit_rate_percent != agreement.card_credit_rate_percent and snap.effective_from <= first:
                    return (f"Rate card credit-card MDR {snap.card_credit_rate_percent}% exceeds agreement "
                            f"{agreement.card_credit_rate_percent}% since {snap.effective_from}, with no amendment",
                            [f"config snapshot {snap.snapshot_id}", f"agreement {agreement.agreement_id}"],
                            f"Restore credit-card MDR {snap.card_credit_rate_percent}% -> {agreement.card_credit_rate_percent}%")
        if pattern == "mdr_on_protected_instrument":
            if instrument == str(Instrument.RUPAY_DEBIT):
                return (f"RuPay debit charged MDR since {first}; protected at every amount",
                        ["MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS"], "Reclassify RuPay debit BINs as nil-MDR")
            return (f"MDR charged on bank-account UPI since {first}; nil-MDR applies until 15 Oct 2026",
                    ["MDR.UPI_P2M.NIL.LEGACY"], "Disable UPI MDR until its 15 Oct 2026 effective date")
        return (f"{pattern.replace('_', ' ')} since {first}", [], action)

    def _horizon(self, pattern: str, instrument: str | None, rule_ids: set[str], as_of: date, merchant_class: str
                 ) -> tuple[float, str]:
        if pattern == "mdr_on_protected_instrument" and instrument == str(Instrument.UPI_P2M_BANK) \
                and "MDR.UPI_P2M.NIL.LEGACY" in rule_ids and merchant_class == "P2M":
            boundary = self.rules.rule("MDR.UPI_P2M.STANDARD.ABOVE_2000").effective_from
            days = max(0, (boundary - as_of).days)
            return (days / 7, f"capped at {boundary - timedelta(days=1)}: from {boundary} the 0.4% MDR is "
                              "legitimate for P2M above Rs 2,000")
        return (float(HORIZON_WEEKS), f"{HORIZON_WEEKS}-week persistence horizon for an uncorrected fault")

    def analyse(self, cases: list[Case], view: MerchantView, as_of: date) -> list[RootCause]:
        families: dict[tuple, list[Case]] = defaultdict(list)
        for case in cases:
            if case.state in (CaseState.CLOSED, CaseState.NO_DISCREPANCY) or case.proof is None:
                continue
            families[(case.pattern, str(case.instrument) if case.instrument else None)].append(case)

        results: list[RootCause] = []
        for (pattern, instrument), family in sorted(families.items(), key=lambda kv: kv[0]):
            dated: list[tuple[date, float]] = []
            for case in family:
                share = case.proof.discrepancy_paise / max(len(case.txn_ids), 1)
                for txn_id in case.txn_ids:
                    txn = view.transactions_by_id.get(txn_id)
                    if txn is not None:
                        dated.append((txn.captured_at.date(), share))
            if not dated:
                continue
            first = min(d for d, _ in dated)
            last = max(d for d, _ in dated)
            proven = [c for c in family if c.proof.authorises_claim]
            escalated_only = not proven
            impact = sum(c.proof.discrepancy_paise for c in proven) if proven else sum(c.proof.discrepancy_paise for c in family)
            window_start = as_of - timedelta(weeks=TRAILING_WEEKS)
            trailing = sum(v for d, v in dated if d > window_start)
            active = (as_of - last).days <= ACTIVE_WITHIN_DAYS
            weekly = int(trailing / TRAILING_WEEKS) if active else 0
            rule_ids = {r for c in family for r in (c.proof.rule_ids if c.proof else ())}
            weeks, note = self._horizon(pattern, instrument, rule_ids, as_of, view.merchant.upi_class)
            cause, evidence, action = self._cause(pattern, instrument, view, first)
            results.append(RootCause(
                root_cause_id=f"RC-{view.merchant_id}-{pattern}-{instrument or 'ANY'}",
                merchant_id=view.merchant_id, discrepancy_type=family[0].discrepancy_type, pattern=pattern,
                instrument=instrument, cause=cause, evidence=evidence, first_seen=first, last_seen=last,
                case_ids=[c.case_id for c in family], historical_impact_paise=impact,
                recovered_paise=sum(c.recovered_paise for c in family), weekly_leakage_paise=weekly,
                horizon_weeks=round(weeks, 1), horizon_note=note if active else "fault no longer occurring",
                projected_leakage_paise=int(weekly * weeks), prevention_action=action,
                status="NEEDS_HUMAN" if escalated_only else "RECOMMENDED",
            ))
        return results
