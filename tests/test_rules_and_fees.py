"""Rule Engine resolution and Fee Engine decomposition."""

from __future__ import annotations

from datetime import date

import pytest

from mfp.core.enums import Instrument, MerchantClass, RoundingPolicy, RuleType
from mfp.fees.engine import FeeEngine, Unresolved
from mfp.rules.engine import RuleEngine, RuleQuery
from mfp.rules.loader import load_fee_rules
from mfp.schemas.ledger import Merchant, MerchantAgreement
from mfp.schemas.rules import AmbiguousRuleError, NoApplicableRuleError, RuleSet

MDR = frozenset({RuleType.MDR, RuleType.NIL_PROTECTION})
GST = frozenset({RuleType.GST, RuleType.GST_EXEMPTION})
HU = RoundingPolicy.HALF_UP


@pytest.fixture(scope="module")
def rules():
    return RuleEngine.from_config()


@pytest.fixture(scope="module")
def fees(rules):
    return FeeEngine(rules)


def merchant(mcc="5411", cls="P2M", eco=False):
    return Merchant(merchant_id="M", legal_name="M", registered_mcc=mcc, city="Mumbai", acquirer_id="ACQ-A",
                    fidelity="FULL", onboarded_on=date(2024, 1, 1), is_ecommerce_participant=eco, upi_class=cls)


AGREEMENTS = [MerchantAgreement(agreement_id="A1", merchant_id="M", signed_on=date(2024, 1, 1), settlement_sla_days=1,
                                card_credit_rate_percent="1.60", card_debit_rate_percent="0.50",
                                netbanking_rate_percent="1.50")]


def q(instrument, amount, on, mcc="5411", cls=MerchantClass.P2M, types=MDR):
    return RuleQuery(types, on, instrument, amount, mcc, cls, "ACQ-A", False)


# -- resolution -----------------------------------------------------------------


def test_statutory_small_value_protection_outranks_legacy(rules):
    assert rules.resolve(q(Instrument.UPI_P2M_BANK, 150_000, date(2026, 9, 20))).rule_id == "MDR.UPI_P2M.NIL.UPTO_2000"
    assert rules.resolve(q(Instrument.UPI_P2M_BANK, 150_000, date(2026, 9, 1))).rule_id == "MDR.UPI_P2M.NIL.LEGACY"


def test_upi_above_2000_regimes(rules):
    assert rules.resolve(q(Instrument.UPI_P2M_BANK, 500_000, date(2026, 10, 14))).rule_id == "MDR.UPI_P2M.NIL.LEGACY"
    assert rules.resolve(q(Instrument.UPI_P2M_BANK, 500_000, date(2026, 10, 15))).rule_id == "MDR.UPI_P2M.STANDARD.ABOVE_2000"
    p2pm = q(Instrument.UPI_P2M_BANK, 500_000, date(2026, 10, 15), cls=MerchantClass.P2PM)
    assert rules.resolve(p2pm).rule_id == "MDR.UPI_P2M.P2PM.NIL"
    fuel = q(Instrument.UPI_P2M_BANK, 500_000, date(2026, 10, 15), mcc="5541")
    assert rules.resolve(fuel).rule_id == "MDR.UPI_P2M.ESSENTIAL.FLAT"


def test_rupay_credit_on_upi_by_mcc_and_date(rules):
    assert rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 7, 1))).rule_id == "MDR.RUPAY_CC_UPI.RETAIL_110"
    assert rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 7, 1), mcc="7230")).rule_id == "MDR.RUPAY_CC_UPI.DEFAULT"
    with pytest.raises(NoApplicableRuleError, match="2026-04-01"):
        rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 4, 1)))


def test_band_cache_never_crosses_a_boundary(rules):
    on = date(2026, 7, 1)
    assert rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 200_000, on)).rule_id == "MDR.RUPAY_CC_UPI.NIL.UPTO_2000"
    assert rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 200_001, on)).rule_id == "MDR.RUPAY_CC_UPI.RETAIL_110"
    assert rules.resolve(q(Instrument.RUPAY_CC_ON_UPI, 199_999, on)).rule_id == "MDR.RUPAY_CC_UPI.NIL.UPTO_2000"


def test_ties_raise_rather_than_pick(rules):
    raw = load_fee_rules().model_dump(mode="json")
    clone = next(r for r in raw["rules"] if r["rule_id"] == "MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS")
    raw["rules"].append({**clone, "rule_id": "MDR.RUPAY_DEBIT.DUPLICATE"})
    tied = RuleEngine(RuleSet.model_validate(raw))
    with pytest.raises(AmbiguousRuleError, match="MDR.RUPAY_DEBIT.DUPLICATE"):
        tied.resolve(q(Instrument.RUPAY_DEBIT, 50_000, date(2026, 5, 1)))


def test_gst_exemption_boundary(rules):
    on = date(2026, 7, 1)
    assert rules.resolve(q(Instrument.CARD_DEBIT, 200_000, on, types=GST)).rule_id == "GST.EXEMPT.PA_SETTLEMENT_UPTO_2000"
    assert rules.resolve(q(Instrument.CARD_DEBIT, 200_001, on, types=GST)).rule_id == "GST.MDR.STANDARD"
    assert rules.resolve(q(Instrument.UPI_P2M_BANK, 100_000, on, types=GST)).rule_id == "GST.MDR.STANDARD"


# -- expected charges ---------------------------------------------------------------


def test_npci_worked_examples_through_the_fee_engine(fees):
    m = merchant()
    on = date(2026, 10, 20)
    for gross, mdr in ((300_000, 1_200), (5_000_000, 20_000), (10_000_000, 30_000)):
        expected, ref = fees.expected_mdr(Instrument.UPI_P2M_BANK, gross, on, m, AGREEMENTS)
        assert expected[HU] == mdr and ref.rule_id == "MDR.UPI_P2M.STANDARD.ABOVE_2000"


def test_contractual_instruments_use_the_agreement_in_force(fees):
    later = AGREEMENTS + [MerchantAgreement(agreement_id="A2", merchant_id="M", signed_on=date(2026, 5, 1),
                                            settlement_sla_days=1, card_credit_rate_percent="1.90",
                                            card_debit_rate_percent="0.50", netbanking_rate_percent="1.50")]
    before, _ = fees.expected_mdr(Instrument.CARD_CREDIT, 100_000, date(2026, 4, 30), merchant(), later)
    after, ref = fees.expected_mdr(Instrument.CARD_CREDIT, 100_000, date(2026, 5, 1), merchant(), later)
    assert before[HU] == 1_600 and after[HU] == 1_900 and ref.rule_id == "AGREEMENT:A2"


# -- decomposition --------------------------------------------------------------------


def decompose(fees, instrument, amount, on, *, mdr=0, gst=0, tcs=0, tds=0, m=None):
    return fees.decompose(instrument, amount, on, m or merchant(), AGREEMENTS, mdr=mdr, gst=gst, tcs=tcs, tds=tds)


def test_early_upi_mdr_is_an_l2_claim_for_mdr_plus_gst(fees):
    (comp,) = decompose(fees, Instrument.UPI_P2M_BANK, 500_000, date(2026, 9, 1), mdr=2_000, gst=360)
    assert comp.discrepancy_type == "L2_NIL_MDR_VIOLATION" and comp.pattern == "mdr_on_protected_instrument"
    assert comp.amount.low == comp.amount.high == 2_360


def test_mcc_band_error_is_the_rate_gap_plus_gst(fees):
    amount = 450_000
    (comp,) = decompose(fees, Instrument.RUPAY_CC_ON_UPI, amount, date(2026, 7, 1), mdr=7_875, gst=1_418)
    assert comp.discrepancy_type == "L1_WRONG_MDR_BAND"
    assert comp.amount[HU] == (7_875 + 1_418) - (4_950 + 891)


def test_gst_on_an_exempt_card_settlement_is_l3_only(fees):
    (comp,) = decompose(fees, Instrument.CARD_DEBIT, 150_000, date(2026, 7, 1), mdr=750, gst=135)
    assert comp.component == "GST" and comp.pattern == "gst_on_exempt_settlement" and comp.amount[HU] == 135


def test_gst_on_gst_is_the_excess_only(fees):
    comps = decompose(fees, Instrument.CARD_CREDIT, 300_000, date(2026, 7, 1), mdr=4_800, gst=1_019)
    assert [(c.component, c.amount[HU]) for c in comps] == [("GST", 1_019 - 864)]


def test_tax_on_a_plain_pa_flow_is_l4(fees):
    (comp,) = decompose(fees, Instrument.UPI_P2M_BANK, 100_000, date(2026, 7, 1), tcs=500, tds=100)
    assert comp.discrepancy_type == "L4_TAX_MISAPPLICATION" and comp.amount[HU] == 600


def test_ppi_charge_rests_on_an_assumed_rule(fees):
    (comp,) = decompose(fees, Instrument.PPI_ON_UPI, 500_000, date(2026, 7, 1), mdr=5_500, gst=990)
    assert comp.assumed and comp.pattern == "unverified_interchange_passthrough"


def test_correct_and_under_charges_produce_nothing(fees):
    # Rs 3,000 is above the Rs 2,000 GST exemption ceiling, so 18% GST on MDR is correct.
    assert decompose(fees, Instrument.CARD_CREDIT, 300_000, date(2026, 7, 1), mdr=4_800, gst=864) == []
    assert decompose(fees, Instrument.CARD_CREDIT, 300_000, date(2026, 7, 1), mdr=3_000, gst=540) == []


def test_gst_on_a_small_card_payment_is_flagged_even_at_the_right_mdr(fees):
    (comp,) = decompose(fees, Instrument.CARD_CREDIT, 100_000, date(2026, 7, 1), mdr=1_600, gst=288)
    assert comp.pattern == "gst_on_exempt_settlement" and comp.amount.low == 288


def test_unresolvable_rule_with_a_charge_is_unresolved(fees):
    result = decompose(fees, Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 4, 1), mdr=7_875, gst=1_418)
    assert isinstance(result, Unresolved)


def test_unresolvable_mdr_rule_with_nothing_charged_still_checks_taxes(fees):
    assert decompose(fees, Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 4, 1)) == []
    (comp,) = decompose(fees, Instrument.RUPAY_CC_ON_UPI, 450_000, date(2026, 4, 1), tcs=2_250, tds=450)
    assert comp.component == "TAX"
