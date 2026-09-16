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

| Fault | Class | Visible where | Scope | Expected action |
|---|---|---|---|---|
| `MCC_MISCONFIG` | L1a | processor rate card (`pricing_mcc`) | per merchant, from 1 Jun 2026 | CLAIM |
| `CONTRACT_RATE_DRIFT` | L1b | processor rate card vs signed agreement | per merchant | CLAIM |
| `UPI_SMALL_MDR` | L2a | settlement lines only | per merchant, 3% of UPI ≤ ₹2,000 mis-tagged as debit card | CLAIM |
| `UPI_MDR_EARLY` | L2b | settlement lines only | **acquirer-wide, ACQ-B, from 20 Aug 2026** | CLAIM |
| `PPI_PASSTHROUGH` | L2e | settlement lines only | per merchant | **ESCALATE** |
| `RUPAY_DEBIT_AS_DEBIT` | L2f | settlement lines only | **acquirer-wide, ACQ-C, from 1 Apr 2026** | CLAIM |

Behavioural faults carry no flag in any observed record. The auditor must find
them from the numbers, as it would in reality. The two acquirer-wide faults are
what later surface as systemic network patterns.

Seed 42: **9,478 plants**, **₹53,151.81 claimable**, **₹6,602.29 escalate-only**.

## The hero merchant

`MER-0001`, *Shree Ganesh Supermart*, Mumbai, MCC 5411, acquirer ACQ-B. Its fault
profile is fixed and documented here rather than drawn, so the demo is stable:

| Fault | From | Plants | Amount |
|---|---|---|---|
| L1a pricing MCC set to 5999 (1.75%) instead of 5411 (1.10%) | 1 Jun 2026 | 126 | ₹3,674.82 |
| L1b credit card rate +25 bps over agreement | 10 Feb 2026 | 2,967 | ₹8,449.64 |
| L2b 0.4% UPI MDR applied before 15 Oct 2026 | 20 Aug 2026 | 275 | ₹4,604.10 |
| L2e wallet interchange passed through | 1 May 2026 | 94 | ₹4,010.80 *(escalate)* |
| **Claimable** | | | **₹16,728.56 across 8 months** |

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
6. **Banking days exclude weekends only**; no holiday list is loaded yet
   (`settlement_rules.json → holiday_calendar`).
7. **Regime is decided by capture date**, not settlement date.
8. **P2PM classification is estimated once at onboarding** from expected volume,
   not recomputed on a rolling three-month basis. Irrelevant inside this window,
   because the P2PM exemption only commences 15 Oct 2026.
9. **Signature-only merchants have fault profiles but no ledger.** Their network
   signatures are synthesized in Block 9.
