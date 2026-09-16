"""The rule configuration must load, and must not contain unsourced rates.

These tests encode the section 8 requirement from the brief: do not silently
invent or assume rates. Anything unverified has to say so in the file itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mfp.core.enums import (
    CalculationKind,
    Instrument,
    MerchantClass,
    RuleType,
    VerificationStatus,
)
from mfp.rules.loader import load_fee_rules, load_raw
from mfp.schemas.rules import Rule


@pytest.fixture(scope="module")
def fee_rules():
    return load_fee_rules()


# -- integrity ----------------------------------------------------------


def test_fee_rules_load(fee_rules):
    assert len(fee_rules.rules) > 15


def test_every_rule_has_a_source_with_a_retrieval_date(fee_rules):
    for rule in fee_rules.rules:
        assert rule.source.authority, rule.rule_id
        assert rule.source.document, rule.rule_id
        assert isinstance(rule.source.retrieved_on, date), rule.rule_id


def test_assumed_rules_must_declare_their_assumptions(fee_rules):
    for rule in fee_rules.rules:
        if rule.verification_status is VerificationStatus.ASSUMED:
            assert rule.assumptions, (
                f"{rule.rule_id} is ASSUMED but lists no assumptions"
            )


def test_assumed_rules_cannot_support_an_auto_claim(fee_rules):
    """The PPI-on-UPI treatment is genuinely unclear, so it must escalate."""
    ppi = _by_id(fee_rules, "MDR.PPI_ON_UPI.INTERCHANGE")
    assert ppi.verification_status is VerificationStatus.ASSUMED
    assert ppi.can_support_auto_claim is False


def test_verified_rules_can_support_a_claim(fee_rules):
    protection = _by_id(fee_rules, "MDR.UPI_P2M.NIL.UPTO_2000")
    assert protection.can_support_auto_claim is True


def test_no_float_rates_survive_loading(fee_rules):
    for rule in fee_rules.rules:
        if rule.value_percent is not None:
            assert isinstance(rule.value_percent, Decimal), rule.rule_id


# -- the researched rules are actually present and correct --------------


def test_upi_nil_protection_stops_at_2000_rupees(fee_rules):
    rule = _by_id(fee_rules, "MDR.UPI_P2M.NIL.UPTO_2000")
    assert rule.calculation is CalculationKind.NIL
    assert rule.scope.amount_range.contains(200_000) is True    # Rs 2,000 exactly
    assert rule.scope.amount_range.contains(200_001) is False   # one paise over


def test_rupay_debit_protection_has_no_upper_bound(fee_rules):
    """The asymmetry a naive system gets wrong -- discrepancy class L2f."""
    rule = _by_id(fee_rules, "MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS")
    assert rule.scope.amount_range.max_paise is None
    assert rule.scope.amount_range.contains(50_000_000) is True  # Rs 5 lakh


def test_upi_mdr_is_not_yet_in_force_on_the_demo_date(fee_rules):
    """Applying it before 15 Oct 2026 is discrepancy class L2b."""
    rule = _by_id(fee_rules, "MDR.UPI_P2M.STANDARD.ABOVE_2000")
    assert rule.in_force_on(date(2026, 9, 16)) is False
    assert rule.in_force_on(date(2026, 10, 14)) is False
    assert rule.in_force_on(date(2026, 10, 15)) is True
    assert rule.value_percent == Decimal("0.4")
    assert rule.cap_paise == 30_000


def test_legacy_blanket_nil_regime_ends_when_the_new_one_starts(fee_rules):
    legacy = _by_id(fee_rules, "MDR.UPI_P2M.NIL.LEGACY")
    assert legacy.in_force_on(date(2026, 10, 14)) is True
    assert legacy.in_force_on(date(2026, 10, 15)) is False


def test_statutory_protection_outranks_commercial_mdr(fee_rules):
    """Precedence is what stops the 0.4% rule from eating a protected transaction."""
    statutory = _by_id(fee_rules, "MDR.UPI_P2M.NIL.UPTO_2000")
    commercial = _by_id(fee_rules, "MDR.UPI_P2M.STANDARD.ABOVE_2000")
    assert statutory.precedence > commercial.precedence


def test_p2pm_exemption_outranks_standard_mdr(fee_rules):
    p2pm = _by_id(fee_rules, "MDR.UPI_P2M.P2PM.NIL")
    standard = _by_id(fee_rules, "MDR.UPI_P2M.STANDARD.ABOVE_2000")
    assert p2pm.precedence > standard.precedence
    assert MerchantClass.P2PM in p2pm.scope.merchant_classes


def test_rupay_cc_on_upi_mcc_table_is_loaded(fee_rules):
    default = _by_id(fee_rules, "MDR.RUPAY_CC_UPI.DEFAULT")
    supermarket = _by_id(fee_rules, "MDR.RUPAY_CC_UPI.RETAIL_110")
    assert default.value_percent == Decimal("1.75")
    assert supermarket.value_percent == Decimal("1.10")
    # An MCC misconfigured into the default band is discrepancy class L1,
    # worth 0.65 percent of every qualifying transaction.
    assert default.precedence < supermarket.precedence


def test_tax_rules_are_gated_on_ecommerce_participation(fee_rules):
    """L4 is about applicability, not about the rate."""
    applies = _by_id(fee_rules, "TCS.ECO.CGST_52")
    does_not = _by_id(fee_rules, "TCS.PA_FLOW.NOT_APPLICABLE")
    assert applies.scope.is_ecommerce_participant is True
    assert does_not.scope.is_ecommerce_participant is False
    assert does_not.precedence > applies.precedence


def test_gst_exemption_for_pa_settlement_is_present(fee_rules):
    rule = _by_id(fee_rules, "GST.EXEMPT.PA_SETTLEMENT_UPTO_2000")
    assert rule.rule_type is RuleType.GST_EXEMPTION
    assert rule.scope.amount_range.max_paise == 200_001
    assert Instrument.CARD_DEBIT in rule.scope.instruments
    # Deliberately does NOT cover UPI: that extension is unresolved, so the
    # Rule Engine will raise and the case will escalate.
    assert Instrument.UPI_P2M_BANK not in rule.scope.instruments


# -- other config files -------------------------------------------------


def test_regulatory_rules_define_the_p2pm_transition():
    raw = load_raw("regulatory_rules.json")
    classification = raw["merchant_classification"]
    assert classification["monthly_inward_limit_paise"] == 10_000_000  # Rs 1 lakh
    assert classification["transition_months"] == 3
    assert classification["assumptions"]


def test_settlement_rules_treat_sla_as_contractual_not_regulatory():
    raw = load_raw("settlement_rules.json")
    assert "commercially negotiated" in raw["description"]
    assert raw["lifecycle"]["unsettled_after_sla_days"] >= 1


def test_regime_boundaries_include_the_october_change():
    raw = load_raw("regulatory_rules.json")
    dates = {b["date"] for b in raw["regime_boundaries"]}
    assert "2026-10-15" in dates
    assert "2026-06-01" in dates


# -- schema guardrails --------------------------------------------------


def test_schema_rejects_an_assumed_rule_with_no_assumptions():
    with pytest.raises(ValidationError, match="must list its assumptions"):
        Rule.model_validate(
            {
                "rule_id": "BAD.RULE",
                "name": "invented rate",
                "rule_type": "MDR",
                "calculation": "FLAT_PERCENT",
                "value_percent": "2.0",
                "effective_from": "2026-01-01",
                "confidence": "LOW",
                "verification_status": "ASSUMED",
                "source": {
                    "authority": "nobody",
                    "document": "made up",
                    "retrieved_on": "2026-09-16",
                },
            }
        )


def test_schema_rejects_a_capped_rule_with_no_cap():
    with pytest.raises(ValidationError, match="needs value_percent and cap_paise"):
        Rule.model_validate(
            {
                "rule_id": "BAD.CAP",
                "name": "cap missing",
                "rule_type": "MDR",
                "calculation": "PERCENT_WITH_CAP",
                "value_percent": "0.4",
                "effective_from": "2026-10-15",
                "confidence": "HIGH",
                "verification_status": "SECONDARY",
                "source": {
                    "authority": "NPCI",
                    "document": "x",
                    "retrieved_on": "2026-09-16",
                },
            }
        )


def _by_id(rule_set, rule_id: str) -> Rule:
    for rule in rule_set.rules:
        if rule.rule_id == rule_id:
            return rule
    raise AssertionError(f"rule not found: {rule_id}")
