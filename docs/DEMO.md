# Live Demo

About six minutes. Every number is computed during the run.

## Before going on stage

```bash
python generate.py --seed 42
```

```bash
python generate.py --seed 42 --no-leakage
```

```bash
python serve.py
```

Run `python rehearse.py` first: it checks the data, runs the full story once,
warms the local model and asks the chat three questions, then prints READY FOR
STAGE or what is broken. Then open http://localhost:8000. Nothing needs the network: `tests/test_new_capabilities.py::test_the_demo_rehearses_with_no_network`
runs all twelve steps with every non-localhost connection blocked. Rehearse once with Wi-Fi off anyway.
If the server stops mid-demo, the page shows a banner and reconnects on its own.

Backup: `python demo.py` tells the same story in a terminal in about 12 seconds.

## Live demo (the default screen)

The command center opens in live operation: no steps, no captions. You narrate
while the system works.

1. **Let a judge pick the merchant.** The dropdown lists all 25 merchants with full
   ledgers, each with a different business and different problems. Press
   **Connect merchant**: the Monitor audits twelve months on its own and cases
   appear in the feed as they are found.
2. **Open any case**, not a chosen one: the drawer shows the evidence chain, the
   rule and the recomputed proof.
3. **Run live.** Virtual time moves one day every 4 seconds. New settlements
   arrive, the Follow-up agent files, chases and re-presents, and money comes
   back, with nobody pressing anything. Press **Pause** to talk.
4. **Impact tab** for the before and after; **Needs review** to file or dismiss
   what the system would not guess; **Merchant chat ↗** for the merchant's side.
5. **Red team** and **Baseline** tabs: run them on demand in front of the judges.
6. **"Is this hard-coded?"** Press **Generate a fresh dataset**: a brand-new random
   seed (about 35 seconds for 25 merchants, 12 months and a clean copy), then
   **Switch to it** and connect any merchant. The figures change; the behaviour
   does not.

## Guided tour (backup)

**Guided tour** in the header swaps in the scripted walk-through below, for when
a presenter wants fixed beats. It runs the same system; press **Exit tour** to
return. Press **Run next step** for each beat (or **Play the tour**).

| # | On screen | Say |
|---|---|---|
| 1 | Big **0**: complaints raised by the merchant. Virtual date 10 Sep 2026. | "Shree Ganesh Supermart hasn't reported anything. No ticket, no question. Paytm checks anyway." |
| 2 | Feed: Monitor finds 256 settlement batches with no audit on record. | "Nobody prompted this. Paytm checks every settlement it sends, as it happens." |
| 3 | Audit: 12 months, 47,915 transactions, 46,889 lines, 256 bank credits matched by UTR. 33 cases, 28 proven, 5 escalated. Headline **₹24,383 found**. Feed also reports late settlements. | "It reconciles every batch back to the bank credit before waiting for anything new. Late money is reported, not claimed: the agreement sets a timeline, not a penalty." |
| 4 | Drawer opens on the showcase case: 101 UPI payments, August 2026. Detection text, rule, rate-card history, precedent. | "The Investigation Agent explains what it sees. It cannot set an amount." |
| 5 | Scroll to **Proof**: Expected ₹0.00 · Actual −₹1,744.78 · Verified difference ₹1,744.78. The "Verified" seal lands. | "The 0.4% UPI MDR was switched on on 20 August. It takes effect on 15 October. Recomputed per transaction, from the published rule, under every rounding convention." |
| 6 | Correction: reference DSP-…, evidence attached, sent to Paytm settlement ops through the workflow. | "Filed because the proof authorised it, and only because of that." |
| 7 | Virtual clock moves to 16 Sep. Feed: 3 new batches, 6 follow-ups, a rejection "as per rate card", 2 re-presentations with the signed agreement, recoveries. | "It keeps working after the first action. It chases, it answers rejections, it waits." |
| 8 | Headline **₹25,712 found · ₹23,252 returned**. Future leakage prevented ₹18,019. Root causes tab: credit card 2.05% → 1.80%; pricing MCC 5999 → 5411; UPI MDR disabled until 15 Oct; batch-close captures; soundbox rental billed after return. 15 corrections filed using what it learned. | "Recovery fixes the symptom. It also names the cause and gets it corrected, so it stops." |
| 9 | Network tab: 100 merchants, MDR on UPI, 77% via ACQ-B. RuPay debit charged MDR: 46 merchants, 100% via ACQ-C. Privacy note: k = 5. | "This isn't one merchant's problem. Agents share the shape of a discrepancy, never a ledger row." |
| 10 | Red team: 32 generated, 23 correctly rejected, 2 escalated, 7 of 7 genuine controls corrected. **False claims 0.** | "We tried to make it file false corrections: RuPay credit at the right MCC rate, exactly ₹2,000, a small merchant at the RBI debit ceiling, rental in the month of return, a network reversal, a P2PM merchant reclassified after three months. It filed none of them, and still caught the real ones." |
| 11 | Baseline: 46,886 lines, **0 discrepancies, 0 corrections, ₹0**. | "Same ledger, nothing wrong: it finds nothing." |
| 12 | Merchant message: *"Is mahine ₹25,712 ki settlement discrepancy identify hui, jismein se ₹23,252 recover ho gayi. 7 case aapke verification ke liye rakhe gaye hain."* | "The merchant didn't ask. Paytm found its own error, proved it, corrected it, and followed it through." |

Optional beats if time allows:

- **Impact tab** (after step 8): with and without the teammate, side by side,
  every figure computed. This is the one-screen summary for the pitch.
- **Merchant chat ↗** (after step 8): a separate phone-style window. It opens
  with Paytm's own message, badged "Paytm found this for you": the money
  already returned, two examples, and what needs the merchant, followed by a card
  for each case that needs them. Press **Haan, correction file karo** on one and
  confirm: it is filed on the merchant's authority and the ops console updates.
  Type "maine soundbox wapas kar diya phir bhi rental kat raha hai" on a merchant
  whose records show no rental problem: a ticket goes to the team and appears in
  **Needs review**. Tap the mic and speak a question, or type. Ask
  "Soundbox rental kyun kata?" and it answers instantly from the device record
  and the rental case, written by the local model (about 6–11 seconds). Ask
  "मेरा साउंडबॉक्स का किराया क्यों कटा?" and it replies in Hindi with the return
  date and the same figures. Ask it for an OTP and it refuses instantly. Every
  bubble says which engine wrote it and that the figures were checked; if a
  guard replaced the model's reply, the bubble says why. For a faster, fully
  scripted run: `MFP_LOCAL_WRITES=0 python serve.py`.

- **Needs review tab**: press **File on my authority** on a PPI case. The state
  machine only lets a person release an escalated case; the correction carries
  the reviewer's name as an attachment. **Dismiss** closes it with no claim.
- **Late settlements tab**: payments that arrived past the agreed date, by month,
  with the worst delay in banking days (weekends and national holidays excluded).

## If a judge asks

- **"Did it just check its own arithmetic?"** The generator's processor and the
  Fee Engine are separate implementations; a static firewall forbids either
  importing the other, with canary tests. Across 389 filed cases the
  proven amount differs from the planted amount by ₹0.01 in total.
- **"What if the reasoning is wrong?"** Open any case: the proof has no input from
  the reasoner. A test runs a reasoner that insists everything is owed; the results
  are identical.
- **"Why not claim the wallet charges?"** No source establishes whether a
  merchant contract passes PPI interchange through. The rule is marked ASSUMED,
  so the agent escalates. Human queue tab.
- **"Why is some money not recovered?"** Settlement ops refuses transactions
  older than 180 days. The agent closes those as unrecovered rather than
  pretending. See [MISSES.md](MISSES.md).

## Recovering from problems

| Problem | Do |
|---|---|
| Browser shows an old state | **Start over** resets the runtime |
| Port in use | `python serve.py --port 8010` |
| n8n container down | Nothing: the workflow falls back to local and the feed says so |
| Want n8n visible | Follow README "Running with n8n"; the feed shows each step executed by n8n |
| Anything else | Switch to `python demo.py` |
