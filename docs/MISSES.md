# Misses and Limitations

Generated from `python evaluate.py --seed 42` and edited only to add the
analysis below. Nothing is omitted.

## Headline

| | |
|---|---|
| Merchants evaluated (full fidelity) | 25 |
| Proven | ₹1,67,681.80 across 396 cases |
| Recovered | ₹1,26,618.33 |
| Closed unrecovered (dispute window) | ₹40,860.87 |
| Escalated to a human | 14 cases, ₹5,926.07 in question |
| Future leakage prevented | ₹1,22,033.05 |
| Late settlements reported (not claimed) | 597 of 597 planted delays |
| **False claims** | **0** |
| Detection misses on observable claimable discrepancies | **0** |

## Reading the misses

**Unobservable (162 components).** Planted on transactions that had not settled
by the data horizon (15 Sep 2026), mostly captured on 14–15 Sep. There is no
settlement line to inspect yet. They are excluded from detection scoring and
listed so the exclusion is visible.

**DETECTION (0).** Every observable claimable discrepancy was owned by a case.
The by-type table shows L2 "detected" 120 below "observable": those are the 120
PPI interchange charges, which are scored as escalations (120 of 120), not
detections.

**PROOF (3 components, ₹3,490.41).** Two payments dropped from settlement
whose instrument was RuPay credit on UPI before 1 Jun 2026: their
non-settlement is certain, but the owed net depends on an MDR for which no
sourced rule exists, so the Proof Engine refuses to compute it and the cases go
to a human. The third is a payment whose evidence chain is incomplete. This is
the gate working as designed.

**ACTION (16 components, ₹2.69).** Proven cases below the ₹1 per-case
materiality floor, held BATCHED because their pattern never reached the ₹500
aggregate floor. Intended behaviour; the floors are configurable.

**RECOVERY (3,043 components, ₹40,860.87).** Corrections that simulated
settlement ops refused as outside its 180-day dispute window. The agent
recognised the rejection as unanswerable and closed the cases as unrecovered
instead of re-presenting. This is the largest gap between identified and
recovered, and it is a property of the counterparty, not of detection or proof.
If Paytm runs this on its own settlements, the window is Paytm's own policy.

**REPORTING (0).** Every planted settlement delay beyond the agreed SLA plus
grace was reported. Delays are not claimed as money: the merchant agreement
sets a timeline but no penalty. A missing payment that later arrives is
withdrawn from correction and reported as a delay instead.

**Amount agreement.** 388 of 389 filed cases equal the planted amount to the
paise; across all filed cases the difference is ₹0.01.

## Known limitations

| Limitation | Effect | Where |
|---|---|---|
| Several 2026 rules are sourced from reputable press, not the circular itself (PIB and NPCI returned HTTP 403) | Marked SECONDARY; would be upgraded to PRIMARY on reading the circulars | FEE_RULES.md §8 |
| PPI-on-UPI merchant pass-through unresolved | Every such charge escalates | FEE_RULES.md §7 |
| GST on the new 0.4% UPI MDR assumed | Only matters from 15 Oct 2026 | FEE_RULES.md §2.2 |
| Rounding convention assumed | Handled by requiring positivity under all policies | PROOF.md |
| Holiday calendar lists national holidays only (ASSUMED); state bank holidays not modelled | A state-holiday delay could be reported one day early; the 2-banking-day grace absorbs it | settlement_rules.json |
| P2PM → P2M reclassification modelled (3 months above ₹1 lakh UPI); the reverse transition is not | Only matters from 15 Oct 2026 | FEE_RULES.md §2.1 |
| RBI debit-card MDR ceilings (2017) applied by previous-year turnover; turnover is taken from the merchant record, not recomputed | Wrong band only if the record is wrong | FEE_RULES.md |
| Device rental terms (₹199/month, 90 free days) are a synthetic contract, not a published Paytm tariff | Rental proofs cite the device record and contract terms | DATA.md |
| TCS/TDS base is gross; 194-O ₹5 lakh floor not modelled | Affects e-commerce participants only | DATA.md |
| Settlement ops is simulated (deterministic policy) | Recovery rates reflect that policy | workflow/claims.py |
| Cognee and Sarvam adapters not exercised against live services (no credentials) | Deterministic fallbacks are what ran | README.md |
| n8n verified live in Docker (every lifecycle step, 0 fallbacks); evaluation numbers use the in-process workflow for speed | Identical decisions either way; the workflow only executes steps | README.md |
| Signature-only merchants' signatures are simulated from fault profiles | Network patterns combine simulated and real agent emissions | DATA.md |

---

## Full evaluation output

### Leaky dataset, seed 42

Dataset `seed-42`, 25 merchant(s), observed through 2026-09-15.

| Stage | Count |
|---|---|
| Planted components | 19,608 (₹178,150.65) |
| Observable by the data horizon | 19,446 (₹177,098.29) |
| Detected | 19,326 |
| Proven | 19,323 |
| Claimed | 19,307 |
| Recovered | 16,264 |
| Escalate-only charges correctly escalated | 120 of 120 |
| **False claims (cases)** | **0** |
| Late settlements reported (not claimed) | 597 of 597 |
| Lookalikes present / claimed | 23,819 / 0 |
| Filed cases whose proven amount equals planted amount exactly | 388 of 389 |
| Planted minus proven across filed cases | ₹0.01 |

### By discrepancy type

| Type | Planted | Unobservable | Observable | Detected | Proven | Claimed | Recovered |
|---|---|---|---|---|---|---|---|
| L1_WRONG_MDR_BAND | 7543 | 42 | 7501 | 7501 | 7501 | 7501 | 6186 |
| L2_NIL_MDR_VIOLATION | 2689 | 55 | 2634 | 2514 | 2514 | 2513 | 2439 |
| L3_GST_BASE_ERROR | 7115 | 50 | 7065 | 7065 | 7065 | 7050 | 6000 |
| L4_TAX_MISAPPLICATION | 2135 | 15 | 2120 | 2120 | 2120 | 2120 | 1549 |
| L5_ORPHAN_REFUND | 17 | 0 | 17 | 17 | 17 | 17 | 14 |
| L6_UNSETTLED_TRANSACTION | 93 | 0 | 93 | 93 | 90 | 90 | 60 |
| L7_DEVICE_RENTAL | 16 | 0 | 16 | 16 | 16 | 16 | 16 |

### Misses by stage

| Stage | Components | Amount | Reasons |
|---|---|---|---|
| RECOVERY | 3043 | ₹40,860.87 | claim ended CLOSED_UNRECOVERED (3043) |
| ACTION | 16 | ₹2.69 | proven but not filed (state BATCHED) (16) |
| PROOF | 3 | ₹3,490.41 | a governing rule could not be resolved: TXN-MER-0006-0005556: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], (1); a governing rule could not be resolved: TXN-MER-0024-0015136: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], (1); evidence incomplete for 1 transaction(s) (1) |

Unobservable by subtype (transactions not yet settled at the data horizon): {'L2b': 41, 'L1b': 39, 'L2e': 1, 'L4a': 15, 'L1c': 2, 'L3a': 50, 'L2f': 5, 'L2a': 8, 'L1a': 1}

### Clean baseline, seed 42

| Metric | Value |
|---|---|
| Proven discrepancies | 0 |
| Claims filed | 0 |
| Recovered | ₹0.00 |
| Escalated | 0 |
| Cases opened | 0 |

### Red team

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
| small_merchant_debit_within_ceiling | L1 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| rental_in_an_active_month | L7 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| rental_for_the_month_of_return | L7 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| reversal_debited_once | L5 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| p2pm_merchant_reclassified_after_three_months | L2 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| late_settlement_beyond_grace | L6 | DO_NOT_CLAIM | DO_NOT_CLAIM |
| control_upi_mdr_before_oct15 | control | CLAIM | CLAIM |
| control_rupay_debit_mdr | control | CLAIM | CLAIM |
| control_p2pm_charged_after_oct15 | control | CLAIM | CLAIM |
| control_duplicate_refund | control | CLAIM | CLAIM |
| control_small_merchant_priced_as_large | control | CLAIM | CLAIM |
| control_rental_after_return | control | CLAIM | CLAIM |
| control_dropped_payment | control | CLAIM | CLAIM |

**32 of 32 correct; false claims 0.**
