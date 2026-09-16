## Leaky dataset, seed 42

Dataset `seed-42`, 25 merchant(s), observed through 2026-09-15.

| Stage | Count |
|---|---|
| Planted components | 18,367 (₹239,800.21) |
| Observable by the data horizon | 18,228 (₹238,723.85) |
| Detected | 18,101 |
| Proven | 18,097 |
| Claimed | 18,085 |
| Recovered | 14,737 |
| Escalate-only charges correctly escalated | 127 of 127 |
| **False claims (cases)** | **0** |
| Lookalikes present / claimed | 23,529 / 45 |
| Filed cases whose proven amount equals planted amount exactly | 356 of 357 |
| Planted minus proven across filed cases | ₹87.57 |

### By discrepancy type

| Type | Planted | Unobservable | Observable | Detected | Proven | Claimed | Recovered |
|---|---|---|---|---|---|---|---|
| L1_WRONG_MDR_BAND | 8008 | 47 | 7961 | 7961 | 7961 | 7961 | 6368 |
| L2_NIL_MDR_VIOLATION | 2759 | 47 | 2712 | 2585 | 2585 | 2582 | 2490 |
| L3_GST_BASE_ERROR | 5404 | 31 | 5373 | 5373 | 5373 | 5365 | 3881 |
| L4_TAX_MISAPPLICATION | 2059 | 14 | 2045 | 2045 | 2045 | 2044 | 1898 |
| L5_ORPHAN_REFUND | 9 | 0 | 9 | 9 | 9 | 9 | 9 |
| L6_UNSETTLED_TRANSACTION | 128 | 0 | 128 | 128 | 124 | 124 | 91 |

### Misses by stage

| Stage | Components | Amount | Reasons |
|---|---|---|---|
| RECOVERY | 3348 | ₹41,431.63 | claim ended CLOSED_UNRECOVERED (2467); claim ended CLOSED (881) |
| ACTION | 12 | ₹2.73 | proven but not filed (state BATCHED) (12) |
| PROOF | 4 | ₹7,023.00 | a governing rule could not be resolved: TXN-MER-0001-0018552: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], on=2026-01-28, instrument=RUPAY_CC_ON_UPI, amount_paise=0, mcc=5411, merchant_class=P2M, is_ecommerce_participant=False (1); a governing rule could not be resolved: TXN-MER-0005-0005408: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], on=2026-01-01, instrument=RUPAY_CC_ON_UPI, amount_paise=200001, mcc=8220, merchant_class=P2M, is_ecommerce_participant=False (1); a governing rule could not be resolved: TXN-MER-0008-0014761: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], on=2026-04-22, instrument=RUPAY_CC_ON_UPI, amount_paise=0, mcc=5411, merchant_class=P2M, is_ecommerce_participant=False (1) |

Unobservable by subtype (transactions not yet settled at the data horizon): {'L1b': 42, 'L2b': 36, 'L1a': 5, 'L4a': 14, 'L3a': 31, 'L2f': 6, 'L2a': 5}

## Clean baseline, seed 42

| Metric | Value |
|---|---|
| Proven discrepancies | 0 |
| Claims filed | 0 |
| Recovered | ₹0.00 |
| Escalated | 0 |
| Cases opened | 0 |

## Red team

| Scenario | Lookalike of | Expected | Actual |
|---|---|---|---|
| rupay_cc_upi_correct_mcc_rate | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| rupay_cc_upi_exactly_2000 | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| card_gst_exempt_at_2000 | L3 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| card_gst_applies_at_2000_01 | L3 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| amended_agreement_rate_increase | L1 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| upi_mdr_after_effective_date | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| upi_mdr_cap_binds | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| essential_fuel_flat_fee | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| eco_merchant_tcs_tds | L4 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| late_settlement_within_grace | L6 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| capture_after_cutoff | L6 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| not_yet_due_at_as_of | L6 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| failed_payment_never_settled | L6 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| partial_refunds_two_events | L5 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| same_amount_two_refunds | L5 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| chargeback_debit | L5 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| ppi_interchange_charged | L2 | ESCALATE | ESCALATE |
| rupay_cc_upi_before_june_charged | L1 | ESCALATE | ESCALATE |
| rounding_convention_half_paise | L1 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| control_upi_mdr_before_oct15 | control | CLAIM | CLAIM |
| control_rupay_debit_mdr | control | CLAIM | CLAIM |
| control_p2pm_charged_after_oct15 | control | CLAIM | CLAIM |
| control_duplicate_refund | control | CLAIM | CLAIM |
| control_dropped_payment | control | CLAIM | CLAIM |

**24 of 24 correct; false claims 0.**
