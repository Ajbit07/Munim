# Settlement Teammate

**An autonomous financial protection teammate for every Paytm merchant.**

It doesn't wait for the merchant to discover a problem. It audits every
settlement, proves every discrepancy from published rules, files the claim,
chases the claims desk, confirms the money came back, and fixes the cause so
it stops happening. When it cannot prove something, it hands it to a human
instead of guessing.

Built for the Paytm Build for India AI Hackathon, Mumbai Edition, Track 3.

---

## What it did on the demo dataset

Seed 42 · 25 merchants with full ledgers · 12 months · 394,532 transactions · 385,708 settlement lines.

| | |
|---|---|
| Discrepancies proven, unprompted | **₹2,26,055.91** across 365 cases |
| Recovered | **₹1,85,875.89** |
| Future leakage prevented | **₹31,847.89** |
| Claims filed | 357, of which **0 were false** |
| Legitimate lookalikes claimed | **0 of 23,648** |
| Ambiguous charges escalated instead of claimed | **127 of 127** |
| Filed amounts equal to the planted amount, to the paise | 356 of 357 |
| Clean baseline (same ledger, nothing planted) | 0 proven · 0 claims · ₹0 recovered · 0 escalated |
| Red team (24 adversarial scenarios incl. 5 genuine controls) | 24 correct · **0 false claims** |

The hero merchant, Shree Ganesh Supermart: **₹41,112.91 found without being
asked, ₹33,643.38 returned**, 7 cases held for human verification, 16 claims
filed with evidence the agent learned from an earlier rejection.

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

Tests (155, about 20 seconds):

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
| Claude (investigation reasoning, reply interpretation) | `pip install -e ".[llm]"`, set `ANTHROPIC_API_KEY`, `MFP_LLM_MODE=record` once, then `replay` on stage | Deterministic reasoner |
| n8n (claim lifecycle execution) | `docker compose up -d`, import `workflow/n8n/claim_lifecycle.json`, `MFP_WORKFLOW=n8n` | In-process workflow; fallback is logged |
| Cognee (case memory graph) | `pip install -e ".[memory]"`, configure Cognee's LLM, `MFP_MEMORY=cognee` | Local SQLite memory |
| Sarvam (Hinglish/Hindi message and speech) | set `SARVAM_API_KEY` | Templated Hinglish |

**Verified in this build:** the deterministic core, all three agents, local
workflow, local memory, templated messaging, the UI and API, and n8n's
fallback when unreachable. **Written but not exercised against live services**
(no credentials were available): the Claude, Cognee and Sarvam adapters, and
the n8n workflow import.

---

## How it works

```
 MONITOR AGENT ─────▶ INVESTIGATION AGENT ─────▶ ║ PROOF GATE ║ ─────▶ FOLLOW-UP AGENT
 discovers work        gathers evidence,          ║ deterministic ║     files, chases,
 (12-month backfill,   asks the reasoner what     ║ no LLM        ║     represents, recovers,
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
src/mfp/workflow/  claims desk, local and n8n lifecycle executors
src/mfp/memory/    SQLite and Cognee memory
src/mfp/network/   signature emitter, pattern engine (k-anonymity)
src/mfp/prevention/ root cause, future leakage
src/mfp/notify/    templated and Sarvam merchant messages
src/mfp/llm/       Claude gateway with record/replay
src/mfp/runtime/   the wired system and read models
src/mfp/data/      observed-data store; generator (processor simulation, ground truth)
src/mfp/redteam/   adversarial scenario generator
src/mfp/evaluation/ harness, red-team runner, evaluation CLI
src/mfp/demo/      demo director and HTTP API (presentation layer)
ui/                command center (vanilla HTML/CSS/JS, no external assets)
workflow/n8n/      n8n claim lifecycle workflow
tools/             static firewall checker
tests/             155 tests including firewall canaries
```
