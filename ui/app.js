"use strict";

// -- formatting -----------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function rupees(paise, withPaise = false) {
  const negative = paise < 0;
  const abs = Math.abs(paise);
  const whole = Math.floor(abs / 100);
  const s = String(whole);
  let grouped = s;
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const parts = [];
    while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
    if (head) parts.unshift(head);
    grouped = parts.join(",") + "," + tail;
  }
  const frac = withPaise ? "." + String(abs % 100).padStart(2, "0") : "";
  return (negative ? "−" : "") + "₹" + grouped + frac;
}

const PATTERNS = {
  mdr_above_mcc_rate: "MDR above the MCC rate",
  mdr_above_agreement: "MDR above the signed agreement",
  mdr_on_protected_instrument: "MDR on a nil-MDR instrument",
  unverified_interchange_passthrough: "Wallet interchange passed through",
  gst_on_exempt_settlement: "GST on an exempt settlement",
  gst_above_standard_base: "GST on the wrong base",
  tax_on_non_eco_flow: "TCS/TDS on a plain PA flow",
  refund_debited_twice: "Refund debited twice",
  refund_without_refund_event: "Refund debit with no refund",
  payment_missing_from_settlement: "Payment missing from settlement",
};
const INSTRUMENTS = {
  UPI_P2M_BANK: "UPI", UPI_LITE: "UPI Lite", RUPAY_CC_ON_UPI: "RuPay credit on UPI", PPI_ON_UPI: "Wallet on UPI",
  RUPAY_DEBIT: "RuPay debit", CARD_DEBIT: "Debit card", CARD_CREDIT: "Credit card", NETBANKING: "Netbanking",
};
const AGENTS = {
  MONITOR_AGENT: "Monitor", INVESTIGATION_AGENT: "Investigation", PROOF_ENGINE: "Proof",
  FOLLOWUP_AGENT: "Follow-up", WORKFLOW_ENGINE: "Workflow", SYSTEM: "System", HUMAN: "Human",
};
const pattern = (p) => PATTERNS[p] || (p || "").replaceAll("_", " ");
const instrument = (i) => INSTRUMENTS[i] || i || "—";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const niceDate = (iso) => new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const chip = (state) => `<span class="chip ${esc(state)}">${esc((state || "").replaceAll("_", " "))}</span>`;

// -- activity feed ---------------------------------------------------------------

const SHOW_STATES = new Set(["FILED", "PARTIALLY_RECOVERED", "ESCALATED", "REJECTED", "REPRESENT", "CLOSED_UNRECOVERED", "FOLLOW_UP"]);

function describe(e) {
  const p = e.payload || {};
  const c = e.case_id ? `${e.case_id} ` : "";
  switch (e.kind) {
    case "system.started": return [`System ready. Workflow: ${p.workflow}. Memory: ${p.memory}. Reasoning: ${p.reasoner}. ${p.network_signatures_received.toLocaleString("en-IN")} network signatures received.`];
    case "merchant.connected": return [`${p.legal_name} connected. Open complaints: ${p.open_complaints}.`];
    case "settlement.activity.detected": return [`${p.batches} settlement batches found with no audit on record. Scheduling a historical audit.`];
    case "backfill.started": return [`Historical audit started: ${p.months} months of settlements.`];
    case "backfill.month.scanned": return p.findings ? [`${p.month}: ${p.lines.toLocaleString("en-IN")} lines reconciled, ${p.findings} findings.`] : null;
    case "backfill.completed": return [`Audit complete: ${p.transactions.toLocaleString("en-IN")} transactions, ${p.batches} batches, ${p.credits_matched} bank credits matched by UTR. ${p.cases_opened} cases opened.`];
    case "settlement.batch.observed": return [`New settlement batch ${p.batch_id}: ${p.lines} lines, ${rupees(p.net_paise)} credited.`];
    case "reconciliation.completed": return p.findings ? [`Reconciled ${p.lines} new lines: ${p.findings} findings.`] : null;
    case "case.discovered": return [`${c}opened: ${pattern(p.pattern)}, ${p.transactions} transactions in ${p.month}.`];
    case "investigation.reasoned": return [`${c}${p.hypothesis}`];
    case "proof.completed": return p.verdict === "PROVEN" ? [`${c}proven: ${rupees(p.discrepancy_paise, true)}.`] : [`${c}${p.verdict === "UNPROVEN" ? "not proven" : "no discrepancy"}${p.unproven_reason ? ": " + p.unproven_reason : "."}`, "alert"];
    case "case.state.changed":
      if (!SHOW_STATES.has(p.to_state)) return null;
      return [`${c}${p.to_state.replaceAll("_", " ").toLowerCase()}: ${p.reason}`, p.to_state === "RECOVERED" ? "money" : p.to_state === "CLOSED_UNRECOVERED" ? "alert" : ""];
    case "claim.held": return [`${c}held: ${p.reason}.`];
    case "workflow.submit": return [`Claim ${p.claim_id} submitted via ${p.engine}, reference ${p.reference}.`];
    case "workflow.follow_up": return [`Follow-up sent on ${p.claim_id}.`];
    case "workflow.response": return [`Claims desk replied on ${p.claim_id}: ${p.status.replaceAll("_", " ").toLowerCase()}${p.reason_code ? " (" + p.reason_code + ")" : ""}.`];
    case "workflow.fallback": return [`n8n unreachable; lifecycle continues on the local workflow.`, "alert"];
    case "recovery.confirmed": return [`${rupees(p.recovered_paise, true)} recovered on ${p.claim_id}.`, "money"];
    case "prevention.requested": return [`Configuration correction requested: ${p.root_cause_id}.`];
    case "prevention.applied": return [`Correction confirmed: ${p.root_cause_id}.`, "money"];
    case "proof_gate.blocked": return [`Proof gate refused a claim on ${e.case_id}: ${(p.problems || []).join("; ")}.`, "alert"];
    case "redteam.completed": return [`Red team: ${p.correct} of ${p.generated} scenarios handled correctly. False claims: ${p.false_claims}.`];
    case "baseline.completed": return [`Clean baseline: ${p.lines.toLocaleString("en-IN")} lines, ${p.proven_cases} discrepancies, ${p.claims_filed} claims.`];
    case "merchant.notified": return [`Merchant told: “${p.text}”`, "money"];
    default: return null;
  }
}

let eventCursor = 0;
const feedItems = [];

async function pullEvents() {
  const res = await fetch(`/api/events?since=${eventCursor}&limit=2000`);
  const body = await res.json();
  eventCursor = body.next;
  let added = 0;
  for (const e of body.events) {
    const d = describe(e);
    if (!d) continue;
    feedItems.push({ day: e.ts.slice(0, 10), html: `<li class="${d[1] || ""}"><span class="time">${e.ts.slice(11, 16)}</span><span class="who ${e.actor}">${AGENTS[e.actor] || e.actor}</span><span class="what">${esc(d[0])}</span></li>` });
    added++;
  }
  if (feedItems.length > 400) feedItems.splice(0, feedItems.length - 400);
  if (added || !feedItems.length) {
    // Newest first, with a date divider heading each day's group.
    const out = [];
    let day = null;
    for (let i = feedItems.length - 1; i >= 0; i--) {
      if (feedItems[i].day !== day) {
        day = feedItems[i].day;
        out.push(`<li class="date-divider">${esc(niceDate(day))}</li>`);
      }
      out.push(feedItems[i].html);
    }
    $("feed").innerHTML = out.join("");
  }
  $("feed-empty").hidden = feedItems.length > 0;
}

// -- state ---------------------------------------------------------------------------

let state = null;

function renderState(s) {
  state = s;
  const m = s.metrics;
  $("merchant-line").textContent = `${s.merchant.legal_name} · ${s.merchant.city} · MCC ${s.merchant.mcc} · ${s.merchant.acquirer}`;
  $("today").textContent = niceDate(s.today);
  $("complaints").textContent = m.open_complaints;

  if (m.identified_paise > 0) {
    $("thesis-line").innerHTML = `${rupees(m.identified_paise)} found without being asked. <span class="cr">${rupees(m.recovered_paise)} returned.</span>`;
  } else if (s.merchant.connected) {
    $("thesis-line").textContent = "Audit complete. Nothing owed.";
  } else {
    $("thesis-line").textContent = "The agent has not started yet.";
  }

  $("m-identified").textContent = rupees(m.identified_paise);
  $("m-recovered").textContent = rupees(m.recovered_paise);
  $("m-prevented").textContent = rupees(m.future_leakage_prevented_paise);
  $("m-progress").textContent = rupees(m.in_progress_paise);
  $("m-proven").textContent = m.proven_cases;
  $("m-active").textContent = m.active_claims;
  $("m-escalated").textContent = m.escalated;
  if (s.redteam) {
    $("m-false").textContent = s.redteam.false_claims;
    $("m-false").classList.toggle("zero", s.redteam.false_claims === 0);
    $("m-false-note").textContent = `${s.redteam.generated} adversarial cases`;
  }

  const rail = $("rail");
  rail.innerHTML = s.steps.map((st, i) => {
    const current = !st.done && (i === 0 || s.steps[i - 1].done);
    return `<li class="${st.done ? "done" : ""} ${current ? "current" : ""}" title="${esc(st.title)}">${st.n}</li>`;
  }).join("");

  const last = s.last_step;
  if (last) {
    $("step-eyebrow").textContent = `Step ${last.n} of ${s.steps.length}`;
    $("step-title").textContent = last.title;
    $("step-narrative").textContent = last.narrative;
  }
  const finished = s.steps.every((st) => st.done);
  $("btn-next").disabled = finished;
  $("btn-next").textContent = finished ? "Story complete" : "Run next step";
  $("btn-advance").disabled = !s.merchant.connected;
}

async function refreshAll() {
  const s = await (await fetch("/api/state")).json();
  renderState(s);
  await pullEvents();
  await renderActiveTab();
}

// -- tabs ---------------------------------------------------------------------------------

let activeTab = "cases";

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  activeTab = b.dataset.tab;
  document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  document.querySelectorAll(".tab-body").forEach((x) => { x.hidden = x.id !== `tab-${activeTab}`; });
  renderActiveTab();
}));

async function renderActiveTab() {
  const el = $(`tab-${activeTab}`);
  const render = TABS[activeTab];
  if (render) el.innerHTML = await render();
  if (activeTab === "cases") {
    el.querySelectorAll("tr[data-case]").forEach((tr) => {
      tr.addEventListener("click", () => openCase(tr.dataset.case));
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openCase(tr.dataset.case); });
    });
  }
}

const TABS = {
  async cases() {
    const cases = await (await fetch("/api/cases")).json();
    if (!cases.length) return `<p class="empty">No cases yet. The Monitor opens them itself during the audit.</p>`;
    return `<table class="ledger"><thead><tr><th>Case</th><th>Month</th><th>What</th><th>Instrument</th><th class="num">Txns</th><th class="num">Amount</th><th>State</th></tr></thead><tbody>` +
      cases.map((c) => `<tr class="clickable" tabindex="0" data-case="${c.case_id}">
        <td class="mono">${c.case_id}</td><td>${c.month}</td><td>${esc(pattern(c.pattern))}</td><td>${instrument(c.instrument)}</td>
        <td class="num">${c.transactions}</td>
        <td class="num ${c.recovered_paise ? "cr" : ""}">${rupees(c.proven_paise || c.disputed_paise, true)}</td>
        <td>${chip(c.state)}</td></tr>`).join("") + `</tbody></table>`;
  },

  async queue() {
    const items = await (await fetch("/api/human-queue")).json();
    if (!items.length) return `<p class="empty">Nothing needs a human. Cases the agent cannot prove land here instead of being claimed.</p>`;
    return items.map((c) => `<div class="card">
      <h3>${c.case_id} · ${esc(pattern(c.pattern))} · ${c.month}</h3>
      <dl class="kv">
        <dt>Reason</dt><dd>${esc(c.escalation?.reason)}</dd>
        <dt>Missing</dt><dd>${esc((c.escalation?.missing || []).join("; ") || "—")}</dd>
        <dt>Agent action</dt><dd>${esc(c.escalation?.agent_action)}</dd>
        <dt>Claim</dt><dd><strong>${esc(c.escalation?.claim)}</strong></dd>
        <dt>Amount in question</dt><dd>${rupees(c.escalation?.disputed_paise || 0, true)}</dd>
      </dl></div>`).join("");
  },

  async causes() {
    const rcs = await (await fetch("/api/root-causes")).json();
    if (!rcs.length) return `<p class="empty">Root causes appear once cases are proven.</p>`;
    return rcs.map((rc) => `<div class="card">
      <h3>${esc(rc.cause)}</h3>
      <dl class="kv">
        <dt>Historical impact</dt><dd>${rupees(rc.historical_impact_paise, true)}</dd>
        <dt>Recovered</dt><dd class="cr">${rupees(rc.recovered_paise, true)}</dd>
        <dt>Leaking now</dt><dd>${rc.weekly_leakage_paise ? rupees(rc.weekly_leakage_paise, true) + " a week" : "stopped"}</dd>
        <dt>Future leakage</dt><dd>${rupees(rc.projected_leakage_paise)} over ${rc.horizon_weeks} weeks <span class="note">(${esc(rc.horizon_note)})</span></dd>
        <dt>Prevention</dt><dd>${esc(rc.prevention_action)} ${chip(rc.status)}</dd>
        <dt>Cases</dt><dd class="mono">${rc.case_ids.join(", ")}</dd>
      </dl></div>`).join("");
  },

  async network() {
    const n = await (await fetch("/api/network")).json();
    const max = Math.max(1, ...n.patterns.map((p) => p.merchants_affected));
    return `<p class="note">${n.signatures.toLocaleString("en-IN")} signatures from merchant agents. No ledger rows, transaction ids or merchant ids are shared: emitters are salted hashes and amounts are bucketed. Patterns shared by fewer than ${n.k} merchants stay hidden (${n.suppressed_below_k} suppressed).</p>
      <table class="ledger"><thead><tr><th>Pattern</th><th>Instrument</th><th class="num">Merchants</th><th></th><th>Concentration</th><th class="num">Impact at least</th><th>Months</th></tr></thead><tbody>` +
      n.patterns.map((p) => `<tr><td>${esc(pattern(p.pattern))}</td><td>${instrument(p.instrument === "-" ? null : p.instrument)}</td>
        <td class="num">${p.merchants_affected}</td><td style="width:90px"><div class="bar"><span style="width:${(100 * p.merchants_affected / max).toFixed(0)}%"></span></div></td>
        <td>${Math.round(p.top_route_share * 100)}% via ${esc(p.top_route)}</td>
        <td class="num">${rupees(p.aggregate_impact_floor_paise)}</td><td>${p.first_month} → ${p.last_month}</td></tr>`).join("") + `</tbody></table>`;
  },

  async redteam() {
    const r = state?.redteam;
    if (!r) return `<p class="empty">Not run yet. Step 10 fires legitimate charges built to look like violations, plus genuine controls, through the real system.</p>`;
    return `<p><strong>${r.generated}</strong> generated · <strong>${r.investigated}</strong> investigated · <strong>${r.correctly_rejected}</strong> correctly rejected · <strong>${r.correctly_escalated}</strong> escalated · <strong>${r.controls_claimed}/${r.controls}</strong> genuine controls claimed · false claims <strong class="${r.false_claims ? "fail" : "pass"}">${r.false_claims}</strong></p>
      <table class="ledger"><thead><tr><th>Scenario</th><th>Looks like</th><th>Expected</th><th>Agent did</th><th></th></tr></thead><tbody>` +
      r.scenarios.map((s) => `<tr><td>${esc(s.explanation)}</td><td>${esc(s.lookalike_of)}</td><td>${s.expected.replaceAll("_", " ")}</td><td>${s.actual.replaceAll("_", " ")}</td>
        <td class="${s.actual === s.expected ? "pass" : "fail"}">${s.actual === s.expected ? "correct" : "wrong"}</td></tr>`).join("") + `</tbody></table>`;
  },

  async baseline() {
    const b = state?.baseline;
    if (!b) return `<p class="empty">Not run yet. Step 11 audits the same merchant's ledger with no leakage planted.</p>`;
    return `<div class="card"><h3>Same ledger, nothing wrong</h3><dl class="kv">
      <dt>Settlement lines</dt><dd>${b.lines.toLocaleString("en-IN")}</dd><dt>Batches</dt><dd>${b.batches}</dd>
      <dt>Discrepancies proven</dt><dd><strong>${b.proven_cases}</strong></dd><dt>Claims filed</dt><dd><strong>${b.claims_filed}</strong></dd>
      <dt>Recovered</dt><dd><strong>${rupees(b.recovered_paise)}</strong></dd><dt>Escalated</dt><dd>${b.escalated}</dd>
    </dl><p class="note">An agent that invents findings would show numbers here.</p></div>`;
  },

  async message() {
    const step = state?.last_step?.key === "notify" ? state.last_step.data : null;
    if (!step) return `<p class="empty">The merchant hears from the agent at the end of the story (step 12).</p>`;
    return `<div class="phone"><div class="bubble">${esc(step.text)}</div>
      <div class="bubble-meta">${esc(step.channel)} · ${esc(step.language)} · ${esc(step.source)}</div>
      <p class="note">${esc(step.english)}</p></div>`;
  },
};

// -- case drill-down --------------------------------------------------------------------------

async function openCase(caseId) {
  const d = await (await fetch(`/api/cases/${caseId}`)).json();
  const c = d.case, p = d.proof, cl = d.claim;
  const proven = p && p.verdict === "PROVEN";
  const stamp = p ? `<div class="stamp ${proven ? "" : "unproven"}">${proven ? "Proven " + rupees(p.discrepancy_paise, true) : p.verdict === "UNPROVEN" ? "Not proven · escalated" : "No discrepancy"}
      <small>${p.proof_id} · ${p.computed_at.slice(0, 10)} · ${p.records} records</small></div>` : "";

  const txRows = d.transactions.map((t) => `<tr><td class="mono">${t.txn_id}</td><td>${instrument(t.instrument)}</td><td class="num">${rupees(t.amount_paise, true)}</td><td>${t.captured_at.slice(0, 16).replace("T", " ")}</td></tr>`).join("");
  const lineRows = d.lines.map((l) => `<tr><td class="mono">${l.batch_id}</td><td>${l.type}</td><td class="num">${rupees(l.gross_paise, true)}</td><td class="num dr">${rupees(l.mdr_paise, true)}</td><td class="num dr">${rupees(l.gst_paise, true)}</td><td class="num dr">${rupees(l.tcs_paise + l.tds_paise, true)}</td><td class="num">${rupees(l.net_paise, true)}</td></tr>`).join("");
  const credits = d.credits.map((k) => `<div class="mono">${k.value_date} · ${k.utr} · ${rupees(k.amount_paise, true)} · ${esc(k.narration)}</div>`).join("");
  const rules = d.rules.map((r) => `<div class="card"><strong>${esc(r.rule_id)}</strong> — ${esc(r.name)}<div class="note">${esc(r.source)}${r.effective_from ? ` · in force from ${r.effective_from}${r.effective_to ? " to " + r.effective_to : ""}` : ""} · ${esc(r.status)} · ${esc(r.confidence)} confidence</div></div>`).join("");
  const calc = p ? p.computation.map((s) => `${s.label}\n  ${s.expression}${s.result_paise != null ? "\n  = " + s.result_paise + " paise" : ""}`).join("\n\n") : "";
  const followups = cl ? cl.steps.filter((s) => ["SUBMIT", "FOLLOW_UP", "RESPONSE", "REPRESENT"].includes(s.step))
    .map((s) => `<div>${s.at.slice(0, 10)} · ${s.step.replaceAll("_", " ").toLowerCase()} ${s.status ? "· " + s.status.toLowerCase().replaceAll("_", " ") : ""}${s.reason_code ? " · " + s.reason_code : ""}${s.added ? " · attached " + s.added.join(", ") : ""} <span class="note">(${esc(s.engine)})</span></div>`).join("") : "";
  const decisions = d.history.filter((h) => ["PROVEN", "UNPROVEN", "ESCALATED", "ACTION_PENDING", "BATCHED", "CLOSED"].includes(h.to_state))
    .map((h) => `<div>${h.to_state.replaceAll("_", " ").toLowerCase()}: ${esc(h.reason)}</div>`).join("");

  $("drawer-body").innerHTML = `
    <div class="step-eyebrow">${c.case_id} · ${c.month}</div>
    <h2 style="font:600 26px var(--display);margin:4px 0">${esc(pattern(c.pattern))} · ${instrument(c.instrument)}</h2>
    <div>${chip(c.state)} <span class="note">${c.txn_count} transactions · opened ${c.opened_at.slice(0, 10)}</span></div>
    <ol class="chain">
      <li><div class="chain-label">Transactions</div><table class="ledger"><tbody>${txRows}</tbody></table>${c.txn_count > d.transactions.length ? `<p class="note">and ${c.txn_count - d.transactions.length} more</p>` : ""}</li>
      <li><div class="chain-label">Settlement</div><table class="ledger"><thead><tr><th>Batch</th><th>Line</th><th class="num">Gross</th><th class="num">MDR</th><th class="num">GST</th><th class="num">TCS+TDS</th><th class="num">Net</th></tr></thead><tbody>${lineRows || `<tr><td colspan="7">No settlement line exists for these transactions.</td></tr>`}</tbody></table>${credits}</li>
      <li><div class="chain-label">Detection</div><div>${esc(c.rationale || "")}</div><div class="note">Reasoning: ${esc(c.reasoner || "—")}. Interpretation only; it never sets the amount.</div></li>
      <li><div class="chain-label">Rule</div>${rules}</li>
      <li><div class="chain-label">Calculation</div><div class="calc">${esc(calc)}</div></li>
      <li><div class="chain-label">Proof</div>${p ? `<div class="figures"><span><small>Expected</small>${rupees(p.expected_paise, true)}</span><span><small>Actual</small>${rupees(p.actual_paise, true)}</span><span><small>Verified difference</small>${rupees(p.discrepancy_paise, true)}</span></div>${stamp}${p.unproven_reason ? `<div class="note">${esc(p.unproven_reason)}</div>` : ""}` : "Not yet proven."}</li>
      <li><div class="chain-label">Decision</div>${decisions || "—"}</li>
      <li><div class="chain-label">Claim</div>${cl ? `<div><span class="mono">${cl.claim_id}</span> · ref <span class="mono">${cl.reference}</span> · ${rupees(cl.amount_paise, true)} via ${esc(cl.workflow)}</div><div class="note">Evidence attached: ${cl.attachments.join(", ")}</div>` : "No claim filed."}</li>
      <li><div class="chain-label">Follow-up</div>${followups || "—"}</li>
      <li><div class="chain-label">Outcome</div>${c.recovered_paise ? `<strong class="cr">${rupees(c.recovered_paise, true)} recovered</strong>` : chip(c.state)}${d.root_cause ? `<div class="note">Root cause: ${esc(d.root_cause.cause)} → ${esc(d.root_cause.prevention_action)}</div>` : ""}</li>
    </ol>`;
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-close").focus();
}

$("drawer-close").addEventListener("click", () => $("drawer").setAttribute("aria-hidden", "true"));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("drawer").setAttribute("aria-hidden", "true"); });

// -- controls ----------------------------------------------------------------------------------

let busy = false;
let autoplay = false;

async function act(url) {
  if (busy) return null;
  busy = true;
  $("busy").hidden = false;
  document.querySelectorAll(".controls .btn").forEach((b) => { if (b.id !== "btn-auto") b.disabled = true; });
  const poll = setInterval(pullEvents, 600);
  try {
    const res = await fetch(url, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || res.statusText);
    return body;
  } catch (err) {
    alert(err.message);
    return null;
  } finally {
    clearInterval(poll);
    busy = false;
    $("busy").hidden = true;
    document.querySelectorAll(".controls .btn").forEach((b) => { b.disabled = false; });
    await refreshAll();
  }
}

$("btn-next").addEventListener("click", async () => {
  const body = await act("/api/demo/next");
  const key = body?.step?.key;
  if (key === "investigation" || key === "proof") openCase(body.step.data.case?.case_id || state.last_step?.data?.case?.case_id || "");
  const tabFor = { network: "network", red_team: "redteam", baseline: "baseline", notify: "message", recovery: "causes", backfill: "cases" }[key];
  if (tabFor) document.querySelector(`.tabs button[data-tab="${tabFor}"]`).click();
});
$("btn-advance").addEventListener("click", () => act("/api/clock/advance?days=1"));
$("btn-reset").addEventListener("click", async () => {
  autoplay = false;
  $("btn-auto").setAttribute("aria-pressed", "false");
  eventCursor = 0; feedItems.length = 0; $("feed").innerHTML = "";
  $("drawer").setAttribute("aria-hidden", "true");
  await act("/api/demo/reset");
});
$("btn-auto").addEventListener("click", async () => {
  autoplay = !autoplay;
  $("btn-auto").setAttribute("aria-pressed", String(autoplay));
  while (autoplay && !state.steps.every((s) => s.done)) {
    $("btn-next").click();
    await new Promise((r) => setTimeout(r, 400));
    while (busy) await new Promise((r) => setTimeout(r, 200));
    await new Promise((r) => setTimeout(r, 3200));
  }
  autoplay = false;
  $("btn-auto").setAttribute("aria-pressed", "false");
});

refreshAll();
