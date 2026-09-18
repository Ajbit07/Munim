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

Then open http://localhost:8000 and press **Run next step**, or **Play the story**.

Headless, the same twelve steps in the terminal (about 12 seconds):

```bash
python demo.py
```

Evaluation against hidden ground truth, the clean baseline and the red team:

```bash
python evaluate.py --seed 42
```

Tests (169, about 2 minutes; the live n8n test is skipped unless `MFP_N8N_LIVE=1`):

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
| Sarvam (Hinglish/Hindi message and speech) | set `SARVAM_API_KEY` | Templated Hinglish |

**Verified live in this build:** n8n 2.39 in Docker executed every claim
lifecycle step of a full run (submit, check, follow-up, re-present, withdraw)
with zero fallbacks. **Written but not run against live services** (no
credentials were available): the Cognee and Sarvam adapters. The demo
and evaluation use their deterministic fallbacks, and a test proves the whole
demo runs with every non-localhost connection blocked.

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
src/mfp/runtime/   the wired system and read models
src/mfp/data/      observed-data store; generator (processor simulation, ground truth)
src/mfp/redteam/   adversarial scenario generator
src/mfp/evaluation/ harness, red-team runner, evaluation CLI
src/mfp/demo/      demo director and HTTP API (presentation layer)
ui/                command center (vanilla HTML/CSS/JS, no external assets)
workflow/n8n/      n8n claim lifecycle workflow
tools/             static firewall checker
tests/             169 tests incl. firewall canaries and an offline rehearsal
```
