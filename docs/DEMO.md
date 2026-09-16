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

Open http://localhost:8000. Rehearse once with Wi-Fi off; nothing needs the network.

If Claude is available, record answers once with `MFP_LLM_MODE=record`, then
present with `MFP_LLM_MODE=replay`. Otherwise the deterministic reasoner is used
and the feed says so.

Backup: `python demo.py` tells the same story in a terminal in about 12 seconds.

## The story

Press **Run next step** for each beat (or **Play the story** to auto-advance).

| # | On screen | Say |
|---|---|---|
| 1 | Big **0**: complaints raised by the merchant. Virtual date 10 Sep 2026. | "Shree Ganesh Supermart hasn't reported anything. No ticket, no question." |
| 2 | Feed: Monitor finds 258 settlement batches with no audit on record. | "Nobody prompted this. The Monitor Agent noticed settlements and no audit." |
| 3 | Audit: 12 months, 47,506 transactions, 46,444 lines, 258 bank credits matched by UTR. 33 cases, 27 proven, 6 escalated. Headline **₹39,695 found**. Cases tab fills. | "It reconciles every batch back to the bank credit before waiting for anything new." |
| 4 | Drawer opens on the showcase case: 119 UPI payments, August 2026. Detection text, rule, rate-card history. | "The Investigation Agent can use a language model to explain. It cannot set an amount." |
| 5 | Scroll to **Proof**: Expected ₹0.00 · Actual −₹1,969.67 · Verified difference ₹1,969.67. The violet stamp lands. | "The acquirer switched on the 0.4% UPI MDR on 20 August. It takes effect on 15 October. Recomputed per transaction, from the published rule, under every rounding convention." |
| 6 | Claim: reference DSP-…, evidence attached, workflow engine named. | "Filed because the proof authorised it — and only because of that." |
| 7 | Virtual clock moves to 16 Sep. Feed: new batches arrive, the desk is silent, follow-ups, a rejection "as per rate card", a re-presentation with the signed agreement, recoveries. | "It keeps working after the first action. It chases, it answers rejections, it waits." |
| 8 | Headline **₹41,112 found · ₹33,643 returned**. Future leakage prevented ₹11,825. Root causes tab: pricing MCC 5999 → 5411; credit card 1.85% → 1.60%; UPI MDR disabled until 15 Oct (horizon capped there). 16 claims filed using what it learned. | "Recovery fixes the symptom. It also names the cause and gets it corrected. And it only counts leakage until 15 October, because after that the charge is legal." |
| 9 | Network tab: 100 merchants, MDR on UPI, 77% via ACQ-B. RuPay debit charged MDR: 46 merchants, 100% via ACQ-C. Privacy note: k = 5; anything below it stays hidden. | "This isn't one merchant's problem. Agents share the shape of a discrepancy, never a ledger row." |
| 10 | Red team: 24 generated, 17 correctly rejected, 2 escalated, 5 of 5 genuine controls claimed. **False claims 0.** | "We tried to make it file false claims. RuPay credit at the right MCC rate, exactly ₹2,000, an amended agreement, a legitimate chargeback, a half-paise rounding edge. It filed none of them, and it still caught the real ones." |
| 11 | Baseline: 46,458 lines, **0 discrepancies, 0 claims, ₹0**. | "Same ledger, nothing wrong: it finds nothing." |
| 12 | Merchant message: *"Is mahine ₹41,112 ki settlement discrepancy identify hui, jismein se ₹33,643 recover ho gayi. 7 case aapke verification ke liye rakhe gaye hain."* | "The merchant didn't ask. The agent found it, proved it, acted on it, and followed it through." |

## If a judge asks

- **"Did it just check its own arithmetic?"** The generator's processor and the
  Fee Engine are separate implementations; a static firewall forbids either
  importing the other, with canary tests. They agree on 18,227 of 18,228
  observable planted components, to the paise.
- **"What if the LLM hallucinates?"** Open any case: the proof has no input from
  the model. A test runs a reasoner that insists everything is owed; the results
  are identical.
- **"Why not claim the wallet charges?"** No source establishes whether a
  merchant contract passes PPI interchange through. The rule is marked ASSUMED,
  so the agent escalates. Human queue tab.
- **"Why is some money not recovered?"** The claims desk refuses transactions
  older than 180 days. The agent closes those as unrecovered rather than
  pretending. See [MISSES.md](MISSES.md).

## Recovering from problems

| Problem | Do |
|---|---|
| Browser shows an old state | **Start over** resets the runtime |
| Port in use | `python serve.py --port 8010` |
| n8n container down | Nothing: the workflow falls back to local and the feed says so |
| Anything else | Switch to `python demo.py` |
