# Fee and Regulatory Rules

Research pass completed **16 September 2026**. Every rate in `config/*.json`
traces to an entry here. Nothing in this system invents a rate.

**Confidence**: HIGH (multiple reputable sources agree, or an issuer document
read directly) · MEDIUM (single reputable secondary source) · LOW (inference).

**Verification status**: `PRIMARY` (issuer or regulator document read directly) ·
`SECONDARY` (reputable press reporting a circular we could not fetch directly) ·
`ASSUMED` (our inference — **may never support an auto-filed claim**; the schema
in `src/mfp/schemas/rules.py` enforces this).

---

## 0. Why this document is longer than expected

The Indian UPI pricing regime changed materially in the six weeks before this
research pass, and the operative change had not yet taken effect on the date of
writing. Three distinct regimes fall inside our twelve-month modelling window:

| Period | UPI P2M (bank account) treatment |
|---|---|
| → 31 May 2026 | Blanket zero MDR, all amounts |
| 1 Jun → 14 Oct 2026 | Blanket zero MDR on bank-account UPI; **RuPay credit-on-UPI MDR begins** above ₹2,000 |
| 15 Oct 2026 → | **0.4% above ₹2,000, capped ₹300**; ≤ ₹2,000 statutorily protected; P2PM small merchants exempt |

This is why `Rule` is temporal (`effective_from` / `effective_to`) rather than a
flat rate table. A system that modelled a single "UPI = 0% MDR" constant would
produce false claims from 15 October onward and miss the discrepancy class
described in §2.3 entirely.

---

## 1. Statutory nil-charge protection

### 1.1 The legal basis changed in August 2026

Section 10A of the Payment and Settlement Systems Act 2007, inserted by the
Finance Act 2019 and read with s.269SU of the Income-tax Act 1961, imposed a
blanket prohibition on MDR for BHIM-UPI and RuPay debit from 1 January 2020.

The **Taxation and Other Laws (Amendment) Bill, 2026**, passed by the Lok Sabha
on 6 August 2026, replaced that blanket prohibition with an **enabling
provision**: the Government may now notify, by executive order, which electronic
payment modes retain statutory protection.

- Confidence **HIGH**, status `SECONDARY`.
- Sources: SCC Online report of the Bill; the Finance Minister's own public
  statement that "the amendment is an enabling provision… It does not impose any
  tax or transaction charge on UPI users."

### 1.2 The 14 September 2026 notification — what is protected today

Ministry of Finance, Department of Financial Services, notification under s.10A:

> **UPI transactions up to ₹2,000** and **RuPay debit card transactions, at any
> amount**, are specified electronic modes. No bank or system provider may
> impose, directly or indirectly, any charge on a person making or receiving
> payment by these modes.

- Confidence **HIGH**, status `SECONDARY`.
- Rules: `MDR.UPI_P2M.NIL.UPTO_2000`, `MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS`.
- Both carry `precedence: 300` — a statutory prohibition outranks every
  commercial rule, and the test suite asserts this ordering.

> **The asymmetry that matters.** UPI protection **stops at ₹2,000**. RuPay debit
> protection **has no upper bound**. A system that treats "protected instrument"
> as a single boolean gets this wrong in both directions. This is discrepancy
> class **L2f**, and `test_rupay_debit_protection_has_no_upper_bound` pins it.

---

## 2. NPCI UPI P2M MDR framework, effective 15 October 2026

Announced 15 September 2026, **one day before this research pass**, and **not in
force on the demo date**.

| Element | Value | Confidence |
|---|---|---|
| Rate, P2M above ₹2,000 | **0.40%** | HIGH |
| Cap | **₹300** (binds at ₹75,000) | HIGH |
| P2M up to ₹2,000 | zero (statutory, §1.2) | HIGH |
| P2P, any amount | zero | HIGH |
| P2PM small merchants | zero, all amounts | MEDIUM |
| Railways, telecom, insurance, fuel, agriculture | **₹5 flat** above ₹2,000 | MEDIUM |
| Mutual funds, securities, brokers | **0.02%**, cap ₹300 | MEDIUM |
| Who pays | the merchant; apps may not pass it to consumers | HIGH |

Published worked examples, which `test_npci_upi_mdr_worked_examples` reproduces
exactly through the Fee Engine primitives:

```
₹3,000    → ₹12
₹50,000   → ₹200
₹1,00,000 → ₹300   (capped, not ₹400)
```

Sources: NPCI framework as reported by Business Standard (15 Sep 2026) and the
Business Today FAQ (15 Sep 2026). Rule `MDR.UPI_P2M.STANDARD.ABOVE_2000`.

### 2.1 P2PM classification — derived state, not configuration

A merchant is **P2PM** while monthly inward UPI receipts stay at or below
**₹1 lakh**. Exceeding that in **three consecutive months** transitions the
merchant to P2M. A single large payment does not by itself change classification.

- Confidence **MEDIUM**, status `SECONDARY` (Business Today; NPCI circular not
  read directly).
- **Declared assumption:** no source describes the *reverse* transition
  (P2M back to P2PM). We do not model it. A case that turns on a reverse
  transition **escalates**.
- Config: `regulatory_rules.json → merchant_classification`.

The rule scope carries `merchant_classes`, so classification decides eligibility
at resolution time. The runtime classifies on a rolling basis
(`Runtime.classify`): from 15 Oct 2026 a merchant declared P2PM is treated as
P2M once its inward UPI exceeded ₹1 lakh in each of the three preceding
calendar months. A merchant declared P2M stays P2M. The red team includes a
P2PM merchant that crossed the limit, so a 0.4% charge on it is not claimed.

### 2.2 Unresolved: GST on the new 0.4% MDR

No source we found states whether 18% GST applies on top of the new UPI MDR.
Card MDR is unambiguously GST-bearing (§4.1). We default to `true` by analogy,
flagged as an assumption. **Any case whose outcome depends on this default must
escalate rather than be claimed.**

### 2.3 Discrepancy class L2b — the early-application case

Because the framework commences **15 October 2026** and our dataset ends
**September 2026**, MDR charged at 0.4% on bank-account UPI P2M *before* that
date is a provable discrepancy: a rule applied ahead of its effective date.
`test_upi_mdr_is_not_yet_in_force_on_the_demo_date` pins the boundary.

---

## 3. RuPay Credit Card on UPI — the MCC table

**Best-sourced item in this research pass.** Taken from a Canara Bank merchant
circular, an issuer document read directly (status `PRIMARY`).

- MDR applies **only above ₹2,000**; nil at or below.
- Effective **1 June 2026**.
- Rate is determined by the merchant's **MCC**.
- MDR is **exclusive of GST**; an additional **18%** applies.
- Collected by **automatic debit from the merchant's settlement account at
  settlement time** — which is precisely the mechanism our reconciliation engine
  must reconstruct.

| Category | MDR | | Category | MDR |
|---|---|---|---|---|
| Public Sector Insurance | 0.50% | | Fuel | 0.75% |
| Agriculture & Allied Inputs | 0.70% | | Contractor Services | 1.10% |
| Railways / Transit / Transportation | 0.70% | | Supermarkets | 1.10% |
| Telecom | 0.70% | | Convenience Store | 1.10% |
| Insurance | 0.70% | | **All other categories** | **1.75%** |
| Education / Government / Post Office | 0.70% | | | |
| Property Management | 0.70% | | | |

**Scope caveat, recorded honestly:** the circular describes these rates as
"indicative" and states the bank "retains the final authority in determining the
applicable MDR." Confidence is HIGH on the table and MEDIUM on universality. The
rule scope supports an `acquirers` list for exactly this reason; this build
applies the table to every acquirer, pending a second issuer's circular.

The gap between the 1.75% default and a correct 1.10% supermarket MCC is
**0.65% of every qualifying transaction** — discrepancy class **L1**, and the
most financially material misconfiguration in the model.

---

## 4. GST

### 4.1 Standard rate

**18%** on the MDR or gateway fee amount — **not** on the transaction value.
Effective fee is therefore `MDR% × 1.18`.

Confidence **HIGH**. Independently corroborated by the Canara Bank circular
(§3), which states MDR is exclusive of 18% GST. Rule `GST.MDR.STANDARD`.

Applying GST to any base other than the MDR amount is discrepancy class **L3**.

### 4.2 The payment-aggregator exemption — a real, citable L3 basis

**CBIC Circular 245/02/2025-GST**, following the 55th GST Council meeting:

> Exemption under **Sl. No. 34 of Notification 12/2017-CTR** is available to
> **RBI-regulated Payment Aggregators** for settlement of an amount **up to
> ₹2,000 in a single transaction** through credit card, debit card, charge card
> or other payment card services — because a PA settling funds to merchants
> falls within the definition of **"acquiring bank"**.

Payment *gateways* that do not settle funds are **not** covered.

- Confidence **HIGH**, status `SECONDARY`.
- Rule `GST.EXEMPT.PA_SETTLEMENT_UPTO_2000`, precedence 200.

**Unresolved and deliberately not resolved:** whether "other payment card
services" extends the exemption to **UPI**. We found no source that settles it,
so UPI is absent from this rule's scope and a test asserts it stays absent.
Inside the modelling window this cannot change an outcome: protected UPI carries
no MDR, so there is no GST base to argue about. It becomes live only for UPI MDR
charged legitimately from 15 Oct 2026, where the standard 18% is applied by
analogy with card MDR (§2.2) and flagged as an assumption.

---

## 5. TCS and TDS — an applicability question, not a rate question

The original specification framed L4 as "TCS applied at the wrong rate." The
research does not support that framing.

| Provision | Rate | Applies to | Confidence |
|---|---|---|---|
| **TCS, CGST s.52** | **0.5%** (0.25 CGST + 0.25 SGST; 0.5 IGST inter-state), reduced from 1% on **10 Jul 2024** | **E-commerce operators.** Per Circular 194/06/2023-GST, where multiple ECOs are involved, the **supplier-side ECO that finally releases payment** collects. | HIGH |
| **TDS, s.194-O** | **0.1%** of gross, reduced from 1% on **1 Oct 2024**; 5% without PAN (s.206AA); ₹5 lakh annual floor for resident individuals/HUF | ECO → e-commerce participant. Recodified as **s.393(1), Table Sl. No. 8(v), Income-tax Act 2025**, in force 1 Apr 2026. | HIGH |
| **Payment aggregator's own obligation** | — | CBDT Circular 20/2023: a payment gateway **need not deduct** under s.194-O where the ECO already did; the obligation rests with whoever makes final payment to the participant. | MEDIUM |

**Conclusion:** on a plain payment-aggregator merchant flow, **neither TCS nor
194-O TDS should appear as a settlement deduction at all.**

L4 is therefore re-scoped: *a tax deducted from a merchant whose flow is not an
e-commerce-operator flow*. The applicability predicate is the per-merchant
`is_ecommerce_participant` flag, and the `NOT_APPLICABLE` rules carry higher
precedence than the rate rules so the predicate governs.

This is a **stronger** discrepancy than a rate error: the deduction should not
exist, so the full amount is recoverable.

---

## 6. Settlement timing — the finding that rewrote L6

The **RBI (Regulation of Payment Aggregators) Directions, 2025** replaced the
earlier rigid **T+1 / Tn+1** mandate. Settlement credits to merchants may now be
effected **per the PA–merchant agreement**, provided the terms are *fair,
equitable and transparently disclosed*. Escrow segregation and pre-settlement
merchant listing remain mandatory.

Confidence **HIGH**, status `SECONDARY`.

**There is no longer a regulatory T+N to test against.** The settlement SLA is a
**per-merchant contract field**, and L6 is proven against the merchant's own
disclosed timeline.

Consequences encoded in `settlement_rules.json`:

- `AWAITING_CYCLE` is a distinct case state. A transaction that has not settled
  but is **not yet due** is never an L6 candidate. Conflating the two would
  generate false claims on day one.
- A transaction becomes L6-eligible only after `unsettled_after_sla_days` beyond
  its contracted settlement date, **and** only after searching every subsequent
  settlement batch.
- Failed-transaction reversals are aged against the RBI TAT circular
  (DPSS.CO.PD No.629/02.01.014/2019-20, 20 Sep 2019) before an L5 orphan-refund
  classification.
- **Declared assumption:** we model weekends plus an explicit per-dataset holiday
  list. Real calendars vary by sponsor bank. A case whose classification flips on
  a single holiday assumption **escalates**.

---

## 6a. Debit-card MDR ceilings by merchant turnover

**RBI circular RBI/2017-18/105 (6 Dec 2017)**, read on rbi.org.in: status
`PRIMARY`, confidence HIGH. It caps debit-card MDR by the merchant's **previous
financial year turnover**:

| Merchant turnover | MDR ceiling | Cap per transaction |
|---|---|---|
| Up to ₹20 lakh | 0.40% | ₹200 |
| Above ₹20 lakh | 0.90% | ₹1,000 |

Rules `MDR_CAP.DEBIT_CARD.SMALL_MERCHANT` and `MDR_CAP.DEBIT_CARD.OTHER_MERCHANT`
(`RuleType.MDR_CAP`, scoped by `turnover_range`). The Fee Engine takes the lower
of the agreement rate and the ceiling; an actual MDR above the ceiling is
pattern `mdr_above_turnover_cap` (L1c). RuPay debit is nil-charge under §1 and
never reaches this rule.

**Declared assumption:** the ceiling still binds non-RuPay debit cards (no later
RBI circular withdrawing it was found). The lower QR-based variant for small
merchants is not modelled separately.

## 6b. Device rental and settlement delay

Neither is a regulatory rate. A **soundbox/EDC rental** is a contract term
(`CONTRACT.DEVICE.RENTAL_TERMS`, an invariant, not a sourced rule): no rental is
due for a month that starts after the device's recorded return, or within its
rental-free period. A **settlement delay** is measured against the merchant's
contracted SLA (§6) plus 2 banking days' grace, with weekends and the national
holiday list excluded. It is reported to settlement operations, never claimed as
money, because the agreement sets a timeline but no penalty.

## 7. PPI / wallet on UPI — the honest gap

NPCI introduced interchange on PPI-funded UPI merchant transactions from
**1 April 2023**: **up to 1.1%** above ₹2,000, **0.5%** for fuel, education,
agriculture and utilities. That much is well sourced (confidence MEDIUM).

What is **not** established by any source we found: whether a merchant sees this
as an MDR line item. The interchange is payable to the wallet issuer within the
acquiring chain, and pass-through is a matter of the acquirer contract, not
regulation. Confidence **LOW**.

Rule `MDR.PPI_ON_UPI.INTERCHANGE` is therefore marked `ASSUMED`. The schema
forbids an `ASSUMED` rule that does not list its assumptions, and
`Rule.can_support_auto_claim` returns `False` for it.

**Every PPI-on-UPI discrepancy escalates. None can be auto-filed.** This is
discrepancy class **L2e**, and it is the clearest demonstration in the product
that the agent knows when not to act.

---

## 8. Open verification items

Ranked by how much a wrong answer would cost:

| # | Item | Why it matters | How to close |
|---|---|---|---|
| 1 | PPI-on-UPI merchant-facing pass-through | Governs whether L2e can ever be claimed rather than escalated | A real acquirer merchant agreement |
| 2 | NPCI's own 15 Oct 2026 circular and FAQ | We relied on press reporting; PIB and NPCI both returned HTTP 403 | Fetch from npci.org.in directly |
| 3 | GST on the new 0.4% UPI MDR | Affects every post-15-Oct GST computation | CBIC clarification or an acquirer's rate card |
| 4 | P2PM reverse transition | Currently unmodelled by declared assumption | NPCI circular |
| 7 | Whether the 2017 debit-card ceilings still bind every acquirer | Governs L1c | A current RBI master direction or acquirer rate card |
| 5 | Whether the Sl. 34 GST exemption reaches UPI | Would widen L3 materially | CBIC clarification |
| 6 | RuPay-CC-on-UPI rate universality across acquirers | Rates are modelled acquirer-scoped because of this | A second issuer's circular |

Items 1, 3, 4 and 5 are all currently handled by **escalation rather than
assumption**, which is the correct behaviour while they remain open.

---

## Sources

- [PIB — zero MDR background](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2114335&reg=48&lang=2) (HTTP 403 at time of access)
- [SCC Online — Taxation and Other Laws (Amendment) Bill, 2026](https://www.scconline.com/blog/post/2026/08/07/taxation-and-other-laws-amendment-bill-2026-lok-sabha/)
- [SCC Online — 14 Sep 2026 s.10A notification](https://www.scconline.com/blog/post/2026/09/15/centre-specifies-rupay-and-upi-transactions-under-payment-systems-act/)
- [Business Today — UPI stays free up to ₹2,000](https://www.businesstoday.in/latest/economy/story/upi-stays-free-finance-ministry-bars-charges-on-transactions-up-to-rs-2000-555475-2026-09-14)
- [Business Standard — NPCI sets 0.4% fee, effective 15 Oct](https://www.business-standard.com/finance/news/npci-sets-0-4-fee-on-upi-merchant-payments-above-2-000-effective-oct-15-126091501061_1.html)
- [Business Today — UPI MDR worked examples and FAQs](https://www.businesstoday.in/personal-finance/story/upi-mdr-rules-rs12-on-rs3000-rs200-on-rs50000-and-rs300-cap-on-rs75000-payments-check-faqs-555724-2026-09-15)
- [Business Today — P2PM small-merchant exemption](https://www.businesstoday.in/personal-finance/news/story/small-merchants-will-not-come-under-upi-mdr-even-above-rs2000-if-they-meet-this-condition-check-details-555727-2026-09-15)
- [RBI/2017-18/105 — Rationalisation of MDR for debit card transactions (PRIMARY)](https://www.rbi.org.in/commonman/English/scripts/Notification.aspx?Id=2620)
- [Canara Bank — MDR on RuPay Credit Card on UPI (PRIMARY)](https://www.canarabank.bank.in/documents/d/guest/mdr-on-rupay-credit-card-on-upi-payments1)
- [CBIC Circular 194/06/2023-GST — TCS, multiple ECOs](https://gstcouncil.gov.in/sites/default/files/2024-06/circular-cgst-194.pdf)
- [Taxscan — CBIC Circular 245/02/2025, PA GST exemption](https://www.taxscan.in/gst-exemption-available-to-rbi-regulated-payment-aggregators-pas-which-involves-handling-money/483812)
- [BDO — CBDT guidelines on s.194-O for payment gateways](https://www.bdo.in/en-gb/insights/alerts-updates/cbdt-issues-additional-guidelines-on-tds-by-e-commerce-operators-under-section-194-o-of-the-it-act)
- [Taxguru — RBI (Regulation of Payment Aggregators) Directions, 2025](https://taxguru.in/rbi/rbi-regulation-payment-aggregators-directions-2025.html)
- [Inc42 — UPI charges notification](https://inc42.com/buzz/govt-notifies-new-rules-no-mdr-for-upi-transactions-under-%E2%82%B92000/)
- [Business Standard — PPI-on-UPI interchange](https://www.business-standard.com/amp/finance/news/merchant-payment-via-wallets-on-upi-to-attract-1-1-interchange-from-apr-1-123032900387_1.html)
