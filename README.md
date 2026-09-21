# Settlement Teammate

**Paytm checks every settlement for its merchants and fixes its own errors
before anyone has to complain.**

An autonomous teammate that audits every settlement a merchant receives, proves
each discrepancy from published rules and the merchant's own agreement, files
a correction with Paytm settlement ops, chases it, confirms the money came back,
and fixes the cause so it stops. When it cannot prove something, it hands the
case to a person instead of guessing.

Why this is real: merchants' most common settlement complaints are unexplained
deductions (soundbox/EDC rental after a device was returned, MDR above the
agreed or regulated rate) and delayed settlements. Today the merchant has to
notice, raise a ticket, and argue. Here nobody has to notice.

Built for the Paytm Build for India AI Hackathon, Mumbai Edition, Track 3.

## What it did on the demo dataset

Seed 42 · 25 merchants with full ledgers · 12 months · 394,974 transactions ·
385,888 settlement lines · 167 payment devices.

| | |
|---|---|
| Discrepancies proven, unprompted | **₹1,67,681.80** across 396 cases (19,323 charges) |
| Recovered | **₹1,26,618.33** |
| Future leakage prevented at the root cause | **₹1,22,033.05** |
| Corrections filed | 389, of which **0 were false** |
| Legitimate lookalikes corrected | **0 of 23,819** |
| Ambiguous charges escalated instead of filed | **120 of 120** |
| Late settlements reported (not claimed as money) | **597 of 597** |
| Filed amounts equal to the planted amount, to the paise | 388 of 389 |
| Clean baseline (same ledger, nothing planted) | 0 proven · 0 corrections · ₹0 · 0 escalated |
| Red team (32 adversarial scenarios incl. 7 genuine controls) | 32 correct · **0 false corrections** |

Seven discrepancy classes: wrong MDR band (incl. the RBI debit-card ceiling
for small merchants), MDR on nil-charge instruments, GST base errors, TCS/TDS
misapplied, refunds debited twice, payments missing from settlement, and
device rental charged after return or inside the free period.

The hero merchant, Shree Ganesh Supermart: **₹25,712.17 found without being
asked, ₹23,252.93 returned**, ₹18,019.54 of future leakage stopped at the root
cause, 7 cases held for human review, 33 late settlements reported.

Every figure is computed by `python evaluate.py --seed 42` from generated
records, agent decisions and workflow outcomes. None is typed into the UI.

---

## Run it

Python 3.12+, Windows / macOS / Linux, fully offline.

```bash
pip install -e ".[dev]"
```

```bash
python generate.py --seed 42
```

```bash
python generate.py --seed 42 --no-leakage
```

```bash
python serve.py
```

Then open http://localhost:8000: the **Settlement Ops console**, for Paytm's
settlement and merchant-support team (merchants never see it; they get the
message, the money, and the chat in their app). A portfolio bar sums every
merchant under watch; the **Merchants** tab is the worklist. It opens in live operation: pick any of the 25
merchants, press **Connect merchant**, then **Run live** and the agents work on
their own. **Generate a fresh dataset** makes a new random seed in about 35
seconds, so a judge can see nothing is pre-built. A scripted **Guided tour** is
one click away for presenting.

Before going on stage, one command checks everything the demo needs, warms the
local model, runs the whole story once and asks the chat three questions:

```bash
python rehearse.py
```

Headless, the same twelve steps in the terminal (about 12 seconds):

```bash
python demo.py
```

Evaluation against hidden ground truth, the clean baseline and the red team:

```bash
python evaluate.py --seed 42
```

Tests (214, about 2 minutes; the live n8n test is skipped unless `MFP_N8N_LIVE=1`):

```bash
python -m pytest
```

Firewalls (proof independence, ground-truth isolation, no wall clock):

```bash
python tools/check_firewall.py
```

### Optional integrations

Each has a local fallback, and the demo never depends on it.

| Integration | Enable | Fallback |
|---|---|---|
| n8n (claim lifecycle execution) | see below | In-process workflow; fallback is logged |
| Cognee (case memory graph) | `pip install -e ".[memory]"`, configure Cognee's LLM, `MFP_MEMORY=cognee` | Local SQLite memory |
| Sarvam (merchant chat, 12 Indian languages, translation, voice; merchant notification) | set `SARVAM_API_KEY` | Local model below, then the checked answer |
| Local model via Ollama (offline backup for the chat) | install Ollama, `ollama pull gemma3:4b` | Checked answer (keyword understanding, Hinglish/English only) |

**Verified live in this build:** n8n 2.39 in Docker executed every claim
lifecycle step of a full run (submit, check, follow-up, re-present, withdraw)
with zero fallbacks. **Written but not run against live services** (no
credentials were available): the Cognee and Sarvam adapters; Sarvam's request
format is checked against docs.sarvam.ai by tests. The local model (gemma3:4b
on Ollama) was run live for the merchant chat. The demo
and evaluation use their deterministic fallbacks, and a test proves the whole
demo runs with every non-localhost connection blocked.

### Merchant chat

Press **Merchant chat ↗** in the command center (or open http://localhost:8000/chat)
for a separate window styled as the Paytm Business app. Paytm speaks first: the
window opens with a message the merchant never asked for, saying how much is
already back in their account and what was fixed. The merchant can then type or
speak (mic button) in any language; the assistant answers from that merchant's
own proven records.

The merchant can also act from the chat, which is their only way to talk to the
teammate:

- **Decide review cases.** Every case the teammate would not decide alone comes
  to the merchant as a card (what happened, the amount, why it needs them) with
  **Haan, correction file karo** or **Nahi, yeh charge sahi hai**. The decision is
  confirmed, recorded with the merchant's name, and carried out by the agents.
- **Report a problem.** "Maine soundbox wapas kar diya, phir bhi rental kat raha
  hai" is checked against the records first: if it is already caught, the chat
  shows its status; if the records do not show it, a ticket goes to Paytm's team
  with the merchant's words, and the chat says so instead of promising money.
  Tickets appear at the top of **Needs review** in the ops console.

What else the merchant can do from the chat:

- **Find a payment**: "8 Sep ka ₹2,113 ka payment kahan hai?" returns the payment
  and whether it settled (batch, date, UTR, charges), arrived late, is not due
  yet, failed, or is missing and under correction.
- **See the proof**: "proof dikhao" (or tapping a case tag) shows the rule and its
  source, the overcharge, an example of the recomputation, and the refund credit.
- **Talk to a person**: the conversation so far is handed to Paytm's team as a
  ticket; their reply arrives in the chat.
- **Appeal** a case closed without recovery, with a reason; the ops desk decides
  and the outcome arrives in the chat.
- **Say it any way**: the model reads every message and recognises these requests
  in any wording or script ("yeh case band kyun kar diya", "मेरा केस वापस खोलो",
  "koi banda hai jisse baat ho sake?"); keywords are only a fast path.
- **Hear back without asking**: refunds landing in the bank (with UTR), replies
  from the team, and appeal outcomes all arrive in the chat on their own.

The command center's **Impact** tab shows the same merchant with and without the
teammate: money lost, problems they would have had to find, lines to check by
hand, and future charges, against money returned, complaints raised (zero) and
causes fixed. Every figure is computed from the audit.

| Job | With `SARVAM_API_KEY` | Without it (offline) |
|---|---|---|
| Understand the question | keywords, then Sarvam | keywords, then the local model (Devanagari, Tamil, unusual phrasing) |
| Write the answer | Sarvam writes it conversationally from the facts, in the merchant's language | the local model writes it the same way (about 6–11 s); `MFP_LOCAL_WRITES=0` shows the checked answer instantly instead |
| Other languages | Sarvam | the local model translates the checked answer |
| Voice (Listen) | Sarvam text-to-speech | the browser's own voice |
| Voice (mic) | Sarvam speech-to-text (Hindi, Hinglish and 10 more) | the browser's speech recognition, where available |

No model decides anything. Facts and a checked answer are computed from the
runtime, and the model writes the reply from them. Guards reject a reply that:

- uses an amount, count or date not in this merchant's records;
- calls money "returned" while it is still under review or in progress;
- leaves out the answer's key figure, or adds amounts to an answer that needs none
  (a "thanks" gets a greeting, never figures);
- loops, rambles, or is in a different language from the question (an English
  question gets an English answer);
- as a translation, changes any figure.

A rejected reply is replaced by the checked answer, and the bubble says why. In a
live run on the demo data, gemma3:4b wrote 7 of 9 replies; the guards replaced one
invented total, one Hinglish answer to an English question, and one mislabelled amount. Requests for an OTP,
PIN or password are refused before any model is asked.

```bash
ollama pull gemma3:4b
```

#### Running with n8n

```bash
docker compose up -d
```

```bash
MSYS_NO_PATHCONV=1 docker compose exec n8n n8n import:workflow --input=/workflows/claim_lifecycle.json
```

```bash
MSYS_NO_PATHCONV=1 docker compose exec n8n n8n publish:workflow --id=mfpClaimLifecyc
```

```bash
docker compose restart n8n
```

```bash
MFP_WORKFLOW=n8n python serve.py --host 0.0.0.0
```

n8n calls back to the API at `host.docker.internal:8000`, so the API must
listen on all interfaces while n8n is in use (`MSYS_NO_PATHCONV=1` only matters
in Git Bash). Stop it afterwards, or allow port 8000 only from the Docker
network in your firewall. `docker compose down` stops n8n.

---

## How it works

```
 MONITOR AGENT ─────▶ INVESTIGATION AGENT ─────▶ ║ PROOF GATE ║ ─────▶ FOLLOW-UP AGENT
 discovers work        gathers evidence,          ║ deterministic ║     files, chases,
 (12-month backfill,   asks the reasoner what     ║ rules only    ║     represents, recovers,
  new batches)         it means, builds a         ╚═══════╤═══════╝     fixes the cause
                       candidate with no amount     PROVEN │ UNPROVEN
                                                           ▼      ▼
                                                        CLAIM   HUMAN QUEUE
```

- **Three agents**, and only three: Monitor, Investigation, Follow-up.
- **Deterministic modules** own financial truth: Rule Engine, Fee Engine,
  Reconciliation Engine, Proof Engine, Case State Machine, Network Pattern
  Engine, Root-Cause and Future-Leakage Calculator, Red-Team Generator,
  Evaluation Harness.
- **No claim without proof.** The Follow-up Agent can only file when the case
  carries a PROVEN result computed from exactly the candidate on the case.
  A persuasive reasoner changes nothing; a test proves it.
- **Rates live in config with sources.** The UPI regime changed in Aug–Sep 2026
  and the new 0.4% MDR starts on 15 Oct 2026, so every rule is dated.
  Unverified rules are marked ASSUMED and can never support a claim.

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Agents, modules, state machine, data flow, adapters, privacy |
| [docs/PROOF.md](docs/PROOF.md) | The proof gate and the financial safety model |
| [docs/FEE_RULES.md](docs/FEE_RULES.md) | Every rate, its source, effective date, confidence and open questions |
| [docs/DATA.md](docs/DATA.md) | The synthetic merchant environment and declared simplifications |
| [docs/DEMO.md](docs/DEMO.md) | The exact live demo sequence |
| [docs/MISSES.md](docs/MISSES.md) | Evaluation results, every miss, known limitations |

## Repository

```
config/            fee, regulatory, settlement and network rules (sourced)
src/mfp/core/      money (integer paise), virtual clock, hash-chained event log, enums
src/mfp/schemas/   rules, observed ledger artifacts, proof contract, network signatures
src/mfp/rules/     Rule Engine
src/mfp/fees/      Fee Engine
src/mfp/reconciliation/  Reconciliation Engine, banking calendar
src/mfp/proof/     Proof Engine
src/mfp/cases/     case state machine
src/mfp/agents/    Monitor, Investigation, Follow-up, reasoners
src/mfp/workflow/  settlement-ops desk (simulated), local and n8n lifecycle executors
src/mfp/memory/    SQLite and Cognee memory
src/mfp/network/   signature emitter, pattern engine (k-anonymity)
src/mfp/prevention/ root cause, future leakage
src/mfp/notify/    templated and Sarvam merchant messages
src/mfp/assistant/ merchant chat: facts, guards, Sarvam and local-model backends
src/mfp/runtime/   the wired system and read models
src/mfp/data/      observed-data store; generator (processor simulation, ground truth)
src/mfp/redteam/   adversarial scenario generator
src/mfp/evaluation/ harness, red-team runner, evaluation CLI
src/mfp/demo/      demo director and HTTP API (presentation layer)
ui/                command center (vanilla HTML/CSS/JS, no external assets)
workflow/n8n/      n8n claim lifecycle workflow
tools/             static firewall checker
tests/             214 tests incl. firewall canaries and an offline rehearsal
```
