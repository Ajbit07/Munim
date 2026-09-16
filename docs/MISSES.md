# Misses and Limitations

Generated from `python evaluate.py --seed 42` and edited only to add the
analysis below. Nothing is omitted.

## Headline

| | |
|---|---|
| Merchants evaluated (full fidelity) | 25 |
| Proven | ₹226,055.91 across 365 cases |
| Recovered | ₹185,875.89 |
| Closed unrecovered (dispute window) | ₹40,037.87 |
| Escalated to a human | 14 cases, ₹5,557.37 in question |
| Future leakage prevented | ₹31,847.89 |
| **False claims** | **0** |
| Detection misses on observable discrepancies | **0** |

## Reading the misses

**Unobservable (139 components).** Planted on transactions that had not settled
by the data horizon (15 Sep 2026), mostly captured on 14–15 Sep. There is no
settlement line to inspect yet. They are excluded from detection scoring and
listed so the exclusion is visible.

**DETECTION (0).** Every observable planted discrepancy was owned by a case.

**PROOF (4 components, ₹7,023.00).** Payments dropped from settlement whose
instrument was RuPay credit on UPI before 1 Jun 2026. Their non-settlement is
certain, but the owed net depends on an MDR for which no sourced rule exists, so
the Proof Engine refuses to compute it and the cases go to a human. This is the
gate working as designed. A future rule for that period, once sourced, would
convert them.

**ACTION (12 components, ₹2.73).** Proven cases below the ₹1 per-case
materiality floor, held BATCHED because their pattern never reached the ₹500
aggregate floor. Intended behaviour; the floors are configurable.

**RECOVERY (2,467 components, ₹40,037.87).** Claims the simulated desk refused
as outside its 180-day dispute window. The agent recognised the rejection as
unanswerable and closed the cases as unrecovered instead of re-presenting. This
is the largest gap between identified and recovered, and it is a property of
the counterparty, not of detection or proof. In a real deployment the
12-month backfill would be most valuable in the first 180 days of history.

**Amount agreement.** 356 of 357 filed cases equal the planted amount to the
paise. The exception (₹87.57) is a dropped wallet-on-UPI payment claimed
conservatively, net of the ASSUMED interchange; see [PROOF.md](PROOF.md).

## Known limitations

| Limitation | Effect | Where |
|---|---|---|
| Several 2026 rules are sourced from reputable press, not the circular itself (PIB and NPCI returned HTTP 403) | Marked SECONDARY; would be upgraded to PRIMARY on reading the circulars | FEE_RULES.md §8 |
| PPI-on-UPI merchant pass-through unresolved | Every such charge escalates | FEE_RULES.md §7 |
| GST on the new 0.4% UPI MDR assumed | Only matters from 15 Oct 2026 | FEE_RULES.md §2.2 |
| Rounding convention assumed | Handled by requiring positivity under all policies | PROOF.md |
| Weekends only, no holiday calendar | A holiday-dependent L6 could be early; grace of 2 banking days mitigates | settlement_rules.json |
| P2PM class estimated at onboarding, not rolling | No effect before 15 Oct 2026 | DATA.md |
| TCS/TDS base is gross; 194-O ₹5 lakh floor not modelled | Affects e-commerce participants only | DATA.md |
| Claims desk is simulated (deterministic policy) | Recovery rates reflect that policy | workflow/claims.py |
| Claude, Cognee, Sarvam adapters not exercised against live services | Deterministic fallbacks are what ran | README.md |
| n8n workflow JSON hand-written, not imported into a running n8n | Unreachable-n8n fallback is tested | workflow/n8n |
| Signature-only merchants' signatures are simulated from fault profiles | Network patterns combine simulated and real agent emissions | DATA.md |

---

## Full evaluation output

### Leaky dataset, seed 42

Dataset `seed-42`, 25 merchant(s), observed through 2026-09-15.

| Stage | Count |
|---|---|
| Planted components | 18,367 (₹239,800.21) |
| Observable by the data horizon | 18,228 (₹238,723.85) |
| Detected | 18,101 |
| Proven | 18,097 |
| Claimed | 18,085 |
| Recovered | 15,618 |
| Escalate-only charges correctly escalated | 127 of 127 |
| **False claims (cases)** | **0** |
| Lookalikes present / claimed | 23,648 / 0 |
| Filed cases whose proven amount equals planted amount exactly | 356 of 357 |
| Planted minus proven across filed cases | ₹87.57 |

### By discrepancy type

| Type | Planted | Unobservable | Observable | Detected | Proven | Claimed | Recovered |
|---|---|---|---|---|---|---|---|
| L1_WRONG_MDR_BAND | 8008 | 47 | 7961 | 7961 | 7961 | 7961 | 6368 |
| L2_NIL_MDR_VIOLATION | 2759 | 47 | 2712 | 2585 | 2585 | 2582 | 2490 |
| L3_GST_BASE_ERROR | 5404 | 31 | 5373 | 5373 | 5373 | 5365 | 4762 |
| L4_TAX_MISAPPLICATION | 2059 | 14 | 2045 | 2045 | 2045 | 2044 | 1898 |
| L5_ORPHAN_REFUND | 9 | 0 | 9 | 9 | 9 | 9 | 9 |
| L6_UNSETTLED_TRANSACTION | 128 | 0 | 128 | 128 | 124 | 124 | 91 |

### Misses by stage

| Stage | Components | Amount | Reasons |
|---|---|---|---|
| RECOVERY | 2467 | ₹40,037.87 | claim ended CLOSED_UNRECOVERED (2467) |
| ACTION | 12 | ₹2.73 | proven but not filed (state BATCHED) (12) |
| PROOF | 4 | ₹7,023.00 | a governing rule could not be resolved: TXN-MER-0001-0018552: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], (1); a governing rule could not be resolved: TXN-MER-0005-0005408: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], (1); a governing rule could not be resolved: TXN-MER-0008-0014761: owed net cannot be computed: no rule applies: types=['MDR', 'NIL_PROTECTION'], (1) |

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
