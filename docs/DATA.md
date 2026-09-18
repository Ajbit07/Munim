# Synthetic Merchant Environment

```bash
python generate.py --seed 42                  # leaky dataset  -> data/generated/seed-42
python generate.py --seed 42 --no-leakage     # clean baseline -> data/generated/seed-42-baseline
python generate.py --seed 42 --merchants 250 --months 12
```

The same seed reproduces every file byte for byte (`test_same_seed_reproduces_every_file_byte_for_byte`).

## Scale (seed 42, defaults)

| Artifact | Rows |
|---|---|
| merchants | 250 (1 hero + 24 cohort at full fidelity, 225 signature-only) |
| transactions | 394,532 |
| settlement lines | 385,708 |
| settlement batches | 6,519 |
| bank credits | 6,815 (including unrelated non-settlement credits) |

Window **15 Sep 2025 → 15 Sep 2026**. Generation takes about 18 seconds.

## What production can see

`data/generated/<dataset>/` holds observed artifacts only: merchants, signed
agreements, processor rate-card history, the transaction ledger, settlement
batches and lines, and the bank statement. `manifest.json` lists row counts and
SHA-256 hashes. **It deliberately omits the leakage flag and every plant count**,
and a test asserts that.

Production code reads through `mfp.data.store.ObservedDataset`, which refuses any
path whose name or parent begins with `_`.

Hidden ground truth lives in `<dataset>/_hidden/ground_truth.json`, readable only
by `mfp.evaluation` (statically enforced, `tools/check_firewall.py`).

**Treat dataset directory names as opaque.** `seed-42-baseline` is named for the
operator's convenience. No production module may branch on a path name.

## How the processor is independent of the auditor

The generator's processor (`mfp.data.generator.tariff`) and the Fee Engine
(Block 2) both read `config/fee_rules.json`, which stands in for published law.
What is independent is **code**: each decides applicability and computes charges
with its own implementation. The firewall forbids the generator from importing
any engine, and forbids production from importing the generator. Agreement
between the two to the paise is therefore evidence, not an echo.

## The controlled baseline

Randomness is split into independent per-merchant streams (ledger, faults,
behaviour, settlement, bank). Turning leakage off changes only the fault stream,
so **the leaky dataset and the baseline contain the identical transaction
ledger**. Only what was charged differs. The baseline is a controlled comparison,
not a different world.

The baseline still contains **2,033 legitimate lookalikes** — correctly charged
RuPay-credit-on-UPI transactions and settlements that landed one banking day
late within grace. An empty baseline would prove nothing about false positives.

## Faults

Planted through dated fault profiles and probability multipliers, never by
editing rows.

| Fault | Class | Visible where | Scope | Expected action | Seed 42 |
|---|---|---|---|---|---|
| `MCC_MISCONFIG` | L1a | processor rate card (`pricing_mcc`) | per merchant, from 1 Jun 2026 | CLAIM | 279 · ₹9,655.49 |
| `CONTRACT_RATE_DRIFT` | L1b | processor rate card vs signed agreement | per merchant | CLAIM | 6,697 · ₹19,434.41 |
| `TURNOVER_BAND_MISAPPLIED` | L1c | debit-card MDR above the RBI small-merchant ceiling (turnover ≤ ₹20 lakh: 0.40%, cap ₹200) | per small merchant | CLAIM | 567 · ₹1,026.22 |
| `UPI_SMALL_MDR` | L2a | settlement lines only | per merchant, UPI ≤ ₹2,000 mis-tagged as debit card | CLAIM | 643 · ₹2,194.06 |
| `UPI_MDR_EARLY` | L2b | settlement lines only | **acquirer-wide, ACQ-B, from 20 Aug 2026** | CLAIM | 956 · ₹16,253.36 |
| `PPI_PASSTHROUGH` | L2e | settlement lines only | per merchant | **ESCALATE** | 121 · ₹5,973.13 |
| `RUPAY_DEBIT_AS_DEBIT` | L2f | settlement lines only | **acquirer-wide, ACQ-C, from 1 Apr 2026** | CLAIM | 969 · ₹9,247.05 |
| `GST_ON_EXEMPT_CARD` | L3a | GST on card payments ≤ ₹2,000 (Sl. 34 exemption) | per merchant | CLAIM | 7,099 · ₹11,527.27 |
| `GST_TAX_ON_TAX` | L3b | GST computed on MDR plus tax | per merchant | CLAIM | 16 · ₹4.02 |
| `TAX_ON_NON_ECO` | L4a | TCS/TDS on a merchant outside any e-commerce operator | per merchant | CLAIM | 2,135 · ₹4,289.92 |
| `DUPLICATE_REFUND_DEBIT` | L5a | one refund debited in two batches | per merchant | CLAIM | 17 · ₹12,650.99 |
| `DROPPED_FROM_BATCH` | L6a | payment in no batch after SLA + grace | per merchant | CLAIM | 93 · ₹82,710.73 |
| `RENTAL_AFTER_RETURN` | L7a | rental line for a month after the device's recorded return | per device | CLAIM | 14 · ₹2,786.00 |
| `RENTAL_DURING_WAIVER` | L7b | rental line inside the rental-free period | per device | CLAIM | 2 · ₹398.00 |
| `SETTLEMENT_DELAY` | D1 | batch dated past SLA + grace | per merchant | **REPORT** (no money owed) | 597 payments · ₹5,93,535.58 held up |

Behavioural faults carry no flag in any observed record. The auditor must find
them from the numbers, as it would in reality. The two acquirer-wide faults are
what later surface as systemic network patterns.

Seed 42: **20,205 plants**, **₹1,72,177.52 claimable**, **₹5,973.13
escalate-only**, **23,819 legitimate lookalikes** (24,099 in the clean baseline).

Also in the ledger, always legitimate: **network reversals** debited once,
**device rentals** billed for months the device was in use (167 devices,
₹199/month after a 90-day free period), and the **national holiday calendar**
(settlements never land on 2 Oct, 26 Jan or 15 Aug).

## The hero merchant

`MER-0001`, *Shree Ganesh Supermart*, Mumbai, MCC 5411, acquirer ACQ-B. Its fault
profile is fixed and documented here rather than drawn, so the demo is stable:

| Fault | From | Plants | Amount |
|---|---|---|---|
| L6a payments captured during batch-close dropped | 11 Dec 2025 | 7 | ₹6,162.19 |
| L1b credit card rate +25 bps over agreement | 10 Feb 2026 | 2,893 | ₹8,394.63 |
| L5a refund debits re-sent after a retry | 5 Apr 2026 | 5 | ₹2,492.31 |
| L7a soundbox returned 10 Apr 2026, rental still debited | May 2026 | 5 | ₹995.00 |
| L2e wallet interchange passed through | 1 May 2026 | 87 | ₹4,358.15 *(escalate)* |
| L1a pricing MCC set to 5999 (1.75%) instead of 5411 (1.10%) | 1 Jun 2026 | 113 | ₹3,409.16 |
| D1 settlement runs slipping past T+1 | 5 Jun 2026 | 33 | ₹36,504.31 held up *(report)* |
| L2b 0.4% UPI MDR applied before 15 Oct 2026 | 20 Aug 2026 | 261 | ₹4,522.95 |
| **Claimable** | | | **₹25,976.24** |

## Declared simplifications

Each is a modelling choice, not a claim about the real rules:

1. **TCS and TDS base is gross transaction value.** Real TCS is on net taxable
   supplies; 194-O on gross sale amount. Affects e-commerce participants only.
2. **The 194-O ₹5 lakh floor for resident individuals/HUFs is not modelled.** All
   synthetic merchants are treated as non-individuals.
3. **RuPay credit on UPI carries no MDR before 1 Jun 2026.** No sourced rule
   exists for that period; see `FEE_RULES.md` §3.
4. **UPI Lite transactions are capped at ₹1,000.**
5. **Refunds do not reverse MDR.** Common commercial practice, not universal.
6. **Banking days exclude weekends and national holidays** (2 Oct, 26 Jan,
   15 Aug; `settlement_rules.json → holiday_calendar`, ASSUMED). State bank
   holidays are not modelled.
7. **Regime is decided by capture date**, not settlement date.
8. **P2PM classification is declared at onboarding and recomputed by the
   auditor** on a rolling basis: three consecutive months above ₹1 lakh of UPI
   makes the merchant P2M. The reverse transition is not modelled.
9. **Signature-only merchants have fault profiles but no ledger.** Their network
   signatures are synthesized in Block 9.
10. **Previous-year turnover is a field on the merchant record** and decides the
    RBI debit-card band; it is not recomputed from the ledger.
11. **Device rental terms are synthetic** (₹199/month, 90 rental-free days,
    billed on the first banking day of the month; a month is chargeable if the
    device was held on its first day). Not a published Paytm tariff.
