# Architecture

## Principle

Agents decide **what to do next**. Deterministic modules decide **what is true**.
The line between them is enforced in code, not in prose.

## The three agents

| Agent | Question it answers | Does | Never does |
|---|---|---|---|
| **Monitor** (`agents/monitor.py`) | What needs attention right now? | On connect, launches the historical audit unprompted. Each clock tick: observes new batches, reconciles only what changed, opens a case for every unowned group of findings. | Judge a finding |
| **Investigation** (`agents/investigation.py`) | What happened, and is there enough evidence? | Gathers records, rules, rate-card history and precedent; asks the Reasoner for an interpretation; builds a Candidate with no amount; submits it to the Proof Engine; routes the verdict to action, human queue or closure. | Set an amount or a verdict |
| **Follow-up** (`agents/followup.py`) | What happens next, and can I finish it alone? | Files through the proof gate; pilots one claim per pattern before batching; attaches evidence memory says wins; chases silence; represents answerable rejections; closes or escalates the rest; confirms recovery; requests the configuration fix. | File without a PROVEN proof computed from the case's own candidate |

There is no fourth agent. Classification, rounding, materiality, network
aggregation and root cause are deterministic modules.

## Deterministic modules

| Module | Responsibility |
|---|---|
| Rule Engine (`rules/engine.py`) | Exactly one rule for a (types, date, instrument, amount, MCC, class, acquirer, e-commerce) query, or `NoApplicableRuleError` / `AmbiguousRuleError`. Cached by amount band. |
| Fee Engine (`fees/engine.py`) | Expected charges under all three rounding policies; decomposition of actual deductions into MDR, GST and tax components. |
| Reconciliation Engine (`reconciliation/engine.py`) | transaction → line → batch → bank credit by UTR; batch integrity; L1–L6 findings; full or incremental. |
| Proof Engine (`proof/engine.py`) | Re-derives every candidate from raw records; the only producer of `ProofResult`. See [PROOF.md](PROOF.md). |
| Case State Machine (`cases/state_machine.py`) | Legal transitions only; every transition recorded with actor, reason, tool, evidence, result. |
| Network Pattern Engine (`network/engine.py`) | Aggregates privacy-safe signatures; surfaces patterns only at k ≥ 5 distinct merchants. |
| Root-Cause & Future-Leakage Calculator (`prevention/root_cause.py`) | Cause from rate-card history or first occurrence; weekly leakage; horizon capped at regulatory boundaries. |
| Red-Team Generator (`redteam/generator.py`) | Legitimate lookalikes and genuine controls in the observed format. |
| Evaluation Harness (`evaluation/harness.py`) | Scores a run against hidden ground truth; the only code that reads it. |

## Data flow

```
generate.py ──▶ observed artifacts (merchants, agreements, rate-card history,
                ledger, settlement batches and lines, bank statement,
                network signatures from other merchants' agents)
            └─▶ _hidden/ground_truth.json ─────────────────────▶ Evaluation Harness only

ObservedDataset / MerchantIndex ──▶ Monitor ──▶ Reconciliation ──▶ Findings
   ──▶ Case (DISCOVERED) ──▶ Investigation ──▶ Reasoner (advisory)
   ──▶ Candidate ──▶ Proof Engine ──▶ ProofResult
   ──▶ PROVEN: Follow-up ──▶ Workflow ──▶ Claims desk ──▶ recovery, prevention
   ──▶ UNPROVEN: Human queue
   ──▶ every step: EventLog (append-only, hash-chained) ──▶ UI feed, drill-down, audit
   ──▶ proven/escalated cases: SignatureEmitter ──▶ Network Pattern Engine
```

## Case states

```
DISCOVERED → INVESTIGATING → CANDIDATE → PROVING → PROVEN → ACTION_PENDING → FILED → WAITING → RECOVERED
                  │                          │         └→ BATCHED → ACTION_PENDING   │  ├→ PARTIALLY_RECOVERED → CLOSED
                  ├→ AWAITING_CYCLE          ├→ UNPROVEN → ESCALATED                 │  ├→ FOLLOW_UP → WAITING | ESCALATED
                  ├→ NO_DISCREPANCY → CLOSED └→ NO_DISCREPANCY → CLOSED               │  └→ REJECTED → REPRESENT → FILED
                  └→ ESCALATED                                                       │              ├→ CLOSED_UNRECOVERED
                                                                                     │              └→ ESCALATED
```

`ALLOWED` in `cases/state_machine.py` is the source of truth; a test replays
every recorded history against it.

## Observability

Every autonomous action is an `Event(seq, ts, actor, kind, case_id, merchant_id,
payload, prev_hash, hash)`. Transitions carry `reason`, `tool`, `evidence`,
`decision`, `action`, `result`, `next_state`. The log has no update or delete
method, and `verify()` names the first event whose chain breaks. The UI activity
feed and the case drill-down are read directly from it.

## Time

Nothing reads the wall clock except `core/clock.py` (enforced statically). The
runtime's `VirtualClock` lets the backfill walk a year in seconds and lets the
follow-up wait be demonstrated live. The runtime also knows where the settlement
feed ends (`data_through`): past that date an absent settlement is "not yet
received", never "missing".

## Adapters and fallbacks

| Seam | Default | Optional | On failure |
|---|---|---|---|
| Reasoner | `DeterministicReasoner` | `ClaudeReasoner` via `LLMGateway` (`claude-opus-5`, structured JSON output, server-side refusal fallback) | Deterministic answer, noted on the event |
| LLM gateway | `off` | `live`, `record`, `replay` (content-addressed cache in `fixtures/llm_cache/`) | Replay miss raises, so it is found in rehearsal |
| Workflow | `LocalWorkflowEngine` | `N8nWorkflowEngine` → `workflow/n8n/claim_lifecycle.json` | Step runs locally; `workflow.fallback` event |
| Memory | `LocalMemoryStore` (SQLite) | `CogneeMemoryStore` (narratives to Cognee; counterparty statistics stay local) | Local store; error recorded |
| Notifier | `TemplatedNotifier` (Hinglish) | `SarvamNotifier` (translate, optional speech) | Template; source marked |

The case state machine stays authoritative in Python with every adapter.

## Firewalls

`tools/check_firewall.py`, with canary tests that plant a violation and assert it is caught:

1. **Proof independence, both directions.** Production packages may not import
   the generator or the evaluation harness; the generator may not import the
   engines. The generator's processor and the Fee Engine are separate
   implementations of the same published rules, so their agreement to the paise
   (18,227 of 18,228 observable components) is evidence, not an echo.
2. **Ground-truth isolation.** Only `mfp.evaluation` reads it and only
   `mfp.data.generator` writes it. The observed store refuses `_hidden/`, and the
   manifest omits the leakage flag and plant counts.
3. **No wall clock** outside `core/clock.py`.

`mfp.demo` (director and HTTP server) is a presentation layer above production:
it may display red-team results, and production may not import it.

## Privacy boundary

A `NetworkSignature` holds type, pattern, instrument, violated rule, processor
route, a coarse merchant segment, month, a bucketed occurrence count and an
impact floor in ₹100 steps. Its emitter is a salted hash. The schema forbids
extra fields, rejects unbucketed amounts and non-hash emitters, and the engine
rejects anything that is not a signature. Patterns below k = 5 merchants are
counted as suppressed and reveal nothing.
