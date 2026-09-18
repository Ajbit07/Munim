# The Proof Gate

> No claim is filed unless the discrepancy is deterministically proven.

## What each component may do

| Component | May | May not |
|---|---|---|
| Reasoner (deterministic) | Explain findings, propose a hypothesis and checks, interpret a claims-desk reply | Produce an amount, a verdict, a Candidate field that carries money |
| Investigation Agent | Build a `Candidate`: transaction ids, component, pattern, rationale | Construct a `ProofResult` |
| Proof Engine | Produce a `ProofResult` | Read the Reconciliation Engine's numbers, read the generator, read ground truth |
| Follow-up Agent | File a claim when `ProofResult.authorises_claim` is true for the case's own candidate | File anything else |

`Candidate` has no amount field (a test asserts it). `ProofResult` is frozen and
validates its own arithmetic: `discrepancy = expected − actual`; PROVEN requires
a positive amount, at least one rule id and source records; UNPROVEN requires a
reason for the human queue.

## What PROVEN requires

For **every** transaction a candidate names, the Proof Engine looks the records
up again by id and requires:

1. **A complete evidence chain.** The ledger event exists. For a charge, exactly
   one settlement line exists and agrees with the ledger on amount and instrument.
   Its batch totals reconcile to its lines. A positive batch traces to a bank
   credit with the same UTR and amount. For an unsettled payment: no line in any
   batch, the contracted due date plus grace has passed, and settlement continued
   after that deadline without it.
2. **Resolved, verified rules.** Every governing rule resolves to exactly one
   rule, and none is ASSUMED. Contract rates come from the agreement in force on
   the capture date.
3. **Independence from rounding.** The rounding convention is itself an ASSUMED
   rule, so each amount is computed under HALF_UP, HALF_EVEN and TRUNCATE. The
   discrepancy must be positive under all three.

The proven amount is the sum of each transaction's **minimum** across rounding
policies: the smallest figure that is true however the processor rounded.

Otherwise the verdict is **UNPROVEN** (with the reason and missing evidence) or
**NOT_A_DISCREPANCY**.

## Component decomposition

A payment line's deductions are attributed in three components, so a claim is
for exactly what is wrong:

```
gst_on_actual = correct GST rule applied to the MDR actually charged
MDR component = (MDR charged + gst_on_actual) − (expected MDR + expected GST)     L1, L2
GST component = GST charged − gst_on_actual                                      L3
TAX component = (TCS + TDS charged) − (expected TCS + TDS)                       L4
```

If GST charged is not above `gst_on_actual`, the MDR component is simply total
MDR+GST charged minus expected. Undercharges are never claimed.

## Worked example (from the demo)

Shree Ganesh Supermart, August 2026, 101 bank-account UPI payments above ₹2,000:

```
TXN-MER-0001-0044496 MDR
  charged 1264 − expected 0 under MDR.UPI_P2M.NIL.LEGACY
  (UPI P2M nil MDR, legacy blanket regime; in force until 14 Oct 2026)
  range across rounding policies 1264..1264
  = 1264 paise
… 100 more transactions, each re-derived identically
total  = 1,74,478 paise

EXPECTED ₹0.00     ACTUAL −₹1,744.78     VERIFIED DIFFERENCE ₹1,744.78
evidence: 101 ledger events, 101 settlement lines, their batches, bank credits by UTR,
          rate-card snapshot; 221 records
```

The acquirer began applying NPCI's 0.4% UPI MDR on 20 Aug 2026. It takes effect
on 15 Oct 2026.

## What the gate refuses, by design

| Situation | Verdict | Why |
|---|---|---|
| Wallet-on-UPI interchange charged | UNPROVEN → human | `MDR.PPI_ON_UPI.INTERCHANGE` is ASSUMED: whether a merchant contract passes it through is not established by any source |
| RuPay credit on UPI charged before 1 Jun 2026 | UNPROVEN → human | No sourced rule exists; the Rule Engine raises instead of assuming zero |
| Bank credit missing for the batch | UNPROVEN → human | The chain from charge to money is broken |
| Payment not in any batch but not yet due | NOT_A_DISCREPANCY | Contracted SLA plus grace has not elapsed |
| Charge half a paise off under another rounding convention | Not a finding | Positive only under a different assumption |
| Rental billed for the month the device was returned in | Not a finding | The device was held on the first day of the month |
| Debit MDR at exactly the RBI ceiling for a small merchant | Not a finding | The ceiling is a maximum, not a target |
| Missing payment that arrives before the correction resolves | WITHDRAWN | Reported as a delay; nothing is owed |
| Rate rose, but a later agreement amendment exists | Not a finding | The agreement in force on the capture date governs |

## Conservative choices

- **Dropped wallet payment.** Its non-settlement is proven, but how much of the
  gross the processor may keep depends on the ASSUMED interchange rule. The claim
  deducts that charge, so it is under-claimed rather than over-claimed.
- **Rental.** Proven only with the device record (activation, free-until,
  return date and pickup reference), the rental line and its batch and bank
  credit. A month is owed back only if it started after the return or inside the
  free period.
- **Human authority.** A person may file an escalated case. The proof gate still
  applies to everything the agent files alone; a human-filed correction carries a
  `human_attestation` attachment with the reviewer's name, and the evaluation
  counts it separately from agent claims.
- **Materiality.** A proven case below ₹1 is held BATCHED, not discarded. Batched
  cases of one pattern are filed together once they reach ₹500.

## Tested guarantees

- A reasoner that insists everything is owed produces identical metrics to the
  deterministic one (`test_a_reasoner_cannot_talk_its_way_past_the_gate`).
- Filing an escalated case raises `ProofGateViolation` and logs `proof_gate.blocked`.
- Every filed claim's proof hash equals its candidate's hash.
- Red team: 32 of 32 scenarios, including 7 genuine controls, 0 false claims.
- Only `Actor.HUMAN` can release an escalated case (`test_only_a_person_can_release_an_escalated_case`).
