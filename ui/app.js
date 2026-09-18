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
  mdr_on_protected_instrument: "MDR on a nil-MDR payment",
  unverified_interchange_passthrough: "Wallet interchange passed on",
  gst_on_exempt_settlement: "GST on an exempt settlement",
  gst_above_standard_base: "GST on the wrong base",
  tax_on_non_eco_flow: "TCS/TDS wrongly deducted",
  refund_debited_twice: "Refund debited twice",
  refund_without_refund_event: "Refund debit with no refund",
  payment_missing_from_settlement: "Payment missing from settlement",
  mdr_above_turnover_cap: "Debit MDR above the RBI ceiling",
  rental_after_return: "Device rental after return",
  rental_during_waiver: "Device rental in the free period",
};
const INSTRUMENTS = {
  UPI_P2M_BANK: "UPI", UPI_LITE: "UPI Lite", RUPAY_CC_ON_UPI: "RuPay credit on UPI", PPI_ON_UPI: "Wallet on UPI",
  RUPAY_DEBIT: "RuPay debit", CARD_DEBIT: "Debit card", CARD_CREDIT: "Credit card", NETBANKING: "Netbanking",
};
const AGENTS = {
  MONITOR_AGENT: "Monitor agent", INVESTIGATION_AGENT: "Investigation agent", PROOF_ENGINE: "Proof engine",
  FOLLOWUP_AGENT: "Follow-up agent", WORKFLOW_ENGINE: "Claim workflow", SYSTEM: "System", HUMAN: "You",
};
const INITIALS = { MONITOR_AGENT: "MO", INVESTIGATION_AGENT: "IN", PROOF_ENGINE: "PR", FOLLOWUP_AGENT: "FU", WORKFLOW_ENGINE: "WF", SYSTEM: "SY", HUMAN: "YOU" };
const STATE_LABELS = {
  RECOVERED: "Recovered", PARTIALLY_RECOVERED: "Partly recovered", ESCALATED: "Needs review", CLOSED_UNRECOVERED: "Not recovered",
  WAITING: "Awaiting ops", FILED: "Correction filed", FOLLOW_UP: "Following up", REPRESENT: "Re-presenting", REJECTED: "Rejected",
  ACTION_PENDING: "Ready to claim", BATCHED: "Held (under ₹1)", CLOSED: "Closed", APPLIED: "Fix applied", REQUESTED: "Fix requested",
  RECOMMENDED: "Fix recommended", NEEDS_HUMAN: "Needs review", WITHDRAWN: "Withdrawn (paid late)",
  RECURRED: "Fix did not hold",
};

const pattern = (p) => PATTERNS[p] || (p || "").replaceAll("_", " ");
const instrument = (i) => INSTRUMENTS[i] || i || "—";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const niceDate = (iso) => new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const chip = (state) => `<span class="pill ${esc(state)}">${esc(STATE_LABELS[state] || (state || "").replaceAll("_", " ").toLowerCase())}</span>`;
const stat = (label, value, cls = "") => `<div class="chip-stat ${cls}"><b>${value}</b>${label}</div>`;

// -- activity feed ---------------------------------------------------------------

const SHOW_STATES = new Set(["FILED", "PARTIALLY_RECOVERED", "ESCALATED", "REJECTED", "REPRESENT", "CLOSED_UNRECOVERED", "FOLLOW_UP", "WITHDRAWN", "CLOSED"]);

function describe(e) {
  const p = e.payload || {};
  const c = e.case_id ? `${e.case_id} ` : "";
  switch (e.kind) {
    case "system.started": return [`Teammate ready. ${p.network_signatures_received.toLocaleString("en-IN")} network signals received. Workflow: ${p.workflow}. Reasoning: ${p.reasoner}.`];
    case "merchant.connected": return [`${p.legal_name} connected. Open complaints: ${p.open_complaints}.`];
    case "settlement.activity.detected": return [`Found ${p.batches} settlement batches with no audit on record. Scheduling a full audit.`];
    case "backfill.started": return [`Auditing ${p.months} months of settlements.`];
    case "backfill.month.scanned": return p.findings ? [`${p.month}: ${p.lines.toLocaleString("en-IN")} settlement lines checked, ${p.findings} issues.`] : null;
    case "backfill.completed": return [`Audit complete: ${p.transactions.toLocaleString("en-IN")} payments, ${p.batches} batches, ${p.credits_matched} bank credits matched by UTR. ${p.cases_opened} cases opened.`];
    case "settlement.batch.observed": return [`New settlement ${p.batch_id}: ${p.lines} payments, ${rupees(p.net_paise)} credited.`];
    case "reconciliation.completed": return p.findings ? [`Checked ${p.lines} new settlement lines: ${p.findings} issues.`] : null;
    case "case.discovered": return [`${c}opened: ${pattern(p.pattern)}, ${p.transactions} payments in ${p.month}.`];
    case "investigation.reasoned": return [`${c}${p.hypothesis}`];
    case "proof.completed": return p.verdict === "PROVEN" ? [`${c}verified: ${rupees(p.discrepancy_paise, true)} owed.`] : [`${c}${p.verdict === "UNPROVEN" ? "not proven" : "no discrepancy"}${p.unproven_reason ? ": " + p.unproven_reason : "."}`, "alert"];
    case "case.state.changed":
      if (!SHOW_STATES.has(p.to_state)) return null;
      return [`${c}${(STATE_LABELS[p.to_state] || p.to_state).toLowerCase()}: ${p.reason}`, p.to_state === "CLOSED_UNRECOVERED" ? "alert" : ""];
    case "claim.held": return [`${c}held: ${p.reason}.`];
    case "workflow.submit": return [`Correction ${p.claim_id} sent to Paytm settlement ops via the ${p.engine} workflow. Reference ${p.reference}.`];
    case "workflow.follow_up": return [`Follow-up sent to settlement ops on ${p.claim_id}.`];
    case "workflow.response": return [`Settlement ops replied on ${p.claim_id}: ${p.status.replaceAll("_", " ").toLowerCase()}${p.reason_code ? " (" + p.reason_code + ")" : ""}.`];
    case "workflow.withdraw": return [`Correction ${p.claim_id} withdrawn: ${p.reason}.`];
    case "settlement.delay.detected": return [`${p.payments} payments settled late (worst ${p.worst_days_late} banking days past the agreed date). Reported to settlement ops; no money is owed for a delay.`, "alert"];
    case "workflow.n8n.step": return [`n8n executed the ${p.action.replaceAll("_", "-")} step for ${p.claim_id}.`];
    case "pattern.recurred": return [`${c}the fix confirmed earlier did not hold: ${pattern(p.pattern).toLowerCase()} is back. Correction re-requested.`, "alert"];
    case "workflow.fallback": return [`n8n unreachable; the claim continues on the local workflow.`, "alert"];
    case "recovery.confirmed": return [`${rupees(p.recovered_paise, true)} back in the merchant's account (${p.claim_id}).`, "money"];
    case "prevention.requested": return [`${c}fix requested so this stops recurring.`];
    case "prevention.applied": return [`${c}fix confirmed. This leak is closed.`, "money"];
    case "proof_gate.blocked": return [`Proof gate refused a claim on ${e.case_id}: ${(p.problems || []).join("; ")}.`, "alert"];
    case "redteam.completed": return [`Red team: ${p.correct} of ${p.generated} tests handled correctly. False claims: ${p.false_claims}.`];
    case "baseline.completed": return [`Clean baseline: ${p.lines.toLocaleString("en-IN")} lines, ${p.proven_cases} discrepancies, ${p.claims_filed} claims.`];
    case "merchant.notified": return [`Merchant notified: “${p.text}”`, "money"];
    default: return null;
  }
}

let eventCursor = 0;
const feedItems = [];

let pulling = null;

function pullEvents() {
  // Serialise pulls: two overlapping requests would read the same cursor and duplicate rows.
  pulling = (pulling || Promise.resolve()).then(pullEventsOnce, pullEventsOnce);
  return pulling;
}

async function pullEventsOnce() {
  const res = await fetch(`/api/events?since=${eventCursor}&limit=2000`);
  const body = await res.json();
  eventCursor = body.next;
  let added = 0;
  feedItems.forEach((item) => { item.html = item.html.replace('<li class="fresh ', '<li class="'); });
  for (const e of body.events) {
    const d = describe(e);
    if (!d) continue;
    feedItems.push({
      day: e.ts.slice(0, 10),
      html: `<li class="fresh ${d[1] || ""}"><span class="avatar ${e.actor}" aria-hidden="true">${INITIALS[e.actor] || "·"}</span>` +
        `<div><span class="who">${AGENTS[e.actor] || e.actor}</span><span class="what">${esc(d[0])}</span></div>` +
        `<span class="time">${e.ts.slice(11, 16)}</span></li>`,
    });
    added++;
  }
  if (feedItems.length > 400) feedItems.splice(0, feedItems.length - 400);
  if (added || !feedItems.length) {
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
let showcaseId = null;

function renderState(s) {
  state = s;
  const m = s.metrics;
  const merchant = s.merchant;
  $("merchant-name").textContent = merchant.legal_name;
  $("merchant-avatar").textContent = merchant.legal_name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  $("merchant-line").textContent = `${merchant.city} · MCC ${merchant.mcc} · ${merchant.acquirer}`;
  const status = $("agent-status");
  status.classList.toggle("on", merchant.connected);
  status.innerHTML = `<span class="dot"></span>${merchant.connected ? "AI teammate active" : "Teammate not started"}`;
  $("today").textContent = niceDate(s.today);
  $("complaints").textContent = m.open_complaints;

  $("hero-found").textContent = rupees(m.identified_paise);
  $("hero-back").textContent = rupees(m.recovered_paise);
  const pct = m.identified_paise ? Math.round((100 * m.recovered_paise) / m.identified_paise) : 0;
  $("hero-bar").style.width = `${pct}%`;
  $("hero-bar-wrap").setAttribute("aria-valuenow", String(pct));
  if (m.identified_paise > 0) {
    $("thesis-line").textContent = `${m.proven_cases} proven cases across ${m.months_affected} months · ${pct}% recovered so far`;
  } else if (merchant.connected) {
    $("thesis-line").textContent = "Audit complete. Every settlement reconciles.";
  } else {
    $("thesis-line").textContent = "The teammate has not started yet.";
  }

  $("m-identified").textContent = rupees(m.identified_paise);
  $("m-recovered").textContent = rupees(m.recovered_paise);
  $("m-prevented").textContent = rupees(m.future_leakage_prevented_paise);
  $("m-progress").textContent = rupees(m.in_progress_paise);
  $("m-proven").textContent = m.proven_cases;
  $("m-active").textContent = m.active_claims;
  $("m-escalated").textContent = m.escalated;
  $("m-escalated-note").textContent = m.human_filed_claims ? `${m.human_filed_claims} filed on your authority` : "";
  $("m-active-note").textContent = m.withdrawn ? `${m.withdrawn} withdrawn: paid late, not lost` : "";
  $("m-progress-note").textContent = m.late_settlements ? `${m.late_settlements} late settlements reported` : "";
  if (s.redteam) {
    $("m-false").textContent = s.redteam.false_claims;
    $("m-false").classList.toggle("zero", s.redteam.false_claims === 0);
    $("m-false-note").textContent = `across ${s.redteam.generated} adversarial tests`;
  } else {
    $("m-false").textContent = "—";
    $("m-false").classList.remove("zero");
    $("m-false-note").textContent = "Not tested yet";
  }

  $("rail").innerHTML = s.steps.map((st, i) => {
    const current = !st.done && (i === 0 || s.steps[i - 1].done);
    return `<li class="${st.done ? "done" : ""} ${current ? "current" : ""}" title="${esc(st.title)}">${st.n}</li>`;
  }).join("");

  const last = s.last_step;
  if (last) {
    $("step-eyebrow").textContent = `Step ${last.n} of ${s.steps.length}`;
    $("step-title").textContent = last.title;
    $("step-narrative").textContent = last.narrative;
  } else {
    $("step-eyebrow").textContent = "Ready";
    $("step-title").textContent = "Run the first step to begin the story";
    $("step-narrative").textContent = "Each step runs the real system. Every number here is computed, not typed in.";
  }
  const finished = s.steps.every((st) => st.done);
  $("btn-next").disabled = finished;
  $("btn-next").textContent = finished ? "Story complete" : "Run next step";
  $("btn-advance").disabled = !merchant.connected;
}

let offlineTimer = null;

function setOffline(offline) {
  $("offline").hidden = !offline;
  if (offline && !offlineTimer) {
    offlineTimer = setInterval(() => refreshAll(), 3000);
  } else if (!offline && offlineTimer) {
    clearInterval(offlineTimer);
    offlineTimer = null;
  }
}

async function refreshAll() {
  try {
    const s = await (await fetch("/api/state")).json();
    renderState(s);
    await pullEvents();
    await renderActiveTab();
    setOffline(false);
  } catch (err) {
    setOffline(true);
  }
}

// -- tabs ---------------------------------------------------------------------------------

let activeTab = "cases";

function selectTab(name) {
  activeTab = name;
  document.querySelectorAll(".segmented button").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === name)));
  document.querySelectorAll(".tab-body").forEach((x) => { x.hidden = x.id !== `tab-${name}`; });
  return renderActiveTab();
}

document.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));

let renderToken = 0;

async function renderActiveTab() {
  const token = ++renderToken;
  const tab = activeTab;
  const el = $(`tab-${tab}`);
  const render = TABS[tab];
  if (!render) return;
  const html = await render();
  if (token !== renderToken) return;  // a newer render started; drop this stale one
  el.innerHTML = html;
  el.querySelectorAll("[data-case]").forEach((row) => {
    row.addEventListener("click", () => openCase(row.dataset.case));
    row.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && ev.target === row) openCase(row.dataset.case); });
  });
  el.querySelectorAll("[data-review]").forEach((btn) => btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    review(btn.dataset.rcase, btn.dataset.review);
  }));
}

const SHIELD = `<svg viewBox="0 0 32 32" width="20" height="20"><path d="M16 3l11 4v8c0 7-4.7 12.3-11 14.5C9.7 27.3 5 22 5 15V7z" fill="#00BAF2"/><path d="M11 15.8l3.4 3.4 6.6-6.8" stroke="#fff" stroke-width="2.8" fill="none" stroke-linecap="round"/></svg>`;

const TABS = {
  async cases() {
    const cases = await (await fetch("/api/cases")).json();
    if (!cases.length) return `<p class="empty">No cases yet. The Monitor agent opens them itself during the audit.</p>`;
    return `<table class="grid"><thead><tr><th>Discrepancy</th><th>Payment type</th><th class="num">Payments</th><th class="num">Amount</th><th>Status</th></tr></thead><tbody>` +
      cases.map((c) => `<tr class="clickable" tabindex="0" data-case="${c.case_id}">
        <td><div class="case-title">${esc(pattern(c.pattern))}</div><div class="case-sub">${c.case_id} · ${c.month}</div></td>
        <td>${instrument(c.instrument)}</td>
        <td class="num">${c.transactions}</td>
        <td class="num amount ${c.recovered_paise ? "cr" : ""}">${rupees(c.proven_paise || c.disputed_paise, true)}</td>
        <td>${chip(c.state)}</td></tr>`).join("") + `</tbody></table>`;
  },

  async queue() {
    const items = await (await fetch("/api/human-queue")).json();
    if (!items.length) return `<p class="empty">Nothing needs your review. Cases the teammate cannot prove land here instead of being filed.</p>`;
    return `<p class="note">The teammate did not file these. It could not prove them from published rules and the merchant's records, so it is asking a person. Filing one records your name as the authority; the teammate then follows it through.</p>` +
      items.map((c) => `<div class="card" data-case="${c.case_id}" tabindex="0" style="cursor:pointer">
      <div class="card-row"><h3>${esc(pattern(c.pattern))} · ${c.month}</h3><span class="pill NEEDS_HUMAN">Not filed</span></div>
      <dl class="kv">
        <dt>Why it stopped</dt><dd>${esc(c.escalation?.reason)}</dd>
        <dt>What is missing</dt><dd>${esc((c.escalation?.missing || []).join("; ") || "—")}</dd>
        <dt>Amount in question</dt><dd><b>${rupees(c.escalation?.disputed_paise || 0, true)}</b></dd>
        <dt>Case</dt><dd class="mono">${c.case_id}</dd>
      </dl>
      <div class="actions">
        <button class="btn btn-solid" data-review="file" data-rcase="${c.case_id}">File on my authority</button>
        <button class="btn btn-outline" data-review="dismiss" data-rcase="${c.case_id}">Dismiss</button>
      </div></div>`).join("");
  },

  async delays() {
    const d = await (await fetch("/api/late-settlements")).json();
    if (!d.payments) return `<p class="empty">No late settlements. Every payment arrived within the agreed timeline.</p>`;
    const months = Object.entries(d.by_month);
    return `<p class="note">Late money is reported to settlement operations, not claimed: the merchant agreement sets a timeline but no penalty. Payments that never arrive are handled as missing-payment corrections instead.</p>
      <div class="summary-strip">${stat("payments settled late", d.payments)}${stat("held up", rupees(d.held_up_paise))}${stat("banking days late on average", d.average_days_late)}</div>
      <table class="grid"><thead><tr><th>Month settled</th><th class="num">Late payments</th><th class="num">Amount</th><th class="num">Worst delay</th></tr></thead><tbody>` +
      months.map(([m, r]) => `<tr><td>${m}</td><td class="num">${r.payments}</td><td class="num amount">${rupees(r.held_up_paise)}</td><td class="num">${r.worst_days_late} banking days</td></tr>`).join("") +
      `</tbody></table><h3 style="margin:18px 0 6px;color:var(--navy);font-size:15px">Most recent</h3>
      <table class="grid"><thead><tr><th>Payment</th><th>Agreed by</th><th>Arrived</th><th class="num">Days late</th><th class="num">Net</th></tr></thead><tbody>` +
      d.recent.map((b) => `<tr><td class="mono">${b.txn_id}</td><td>${b.deadline}</td><td>${b.settled_on}</td><td class="num">${b.banking_days_late}</td><td class="num">${rupees(b.net_paise, true)}</td></tr>`).join("") +
      `</tbody></table>`;
  },

  async causes() {
    const rcs = await (await fetch("/api/root-causes")).json();
    if (!rcs.length) return `<p class="empty">Root causes appear once cases are proven.</p>`;
    return rcs.map((rc) => `<div class="card">
      <div class="card-row"><h3>${esc(rc.cause)}</h3>${chip(rc.status)}</div>
      <dl class="kv">
        <dt>Fix</dt><dd><b>${esc(rc.prevention_action)}</b></dd>
        <dt>Impact so far</dt><dd>${rupees(rc.historical_impact_paise, true)} · <span class="cr">${rupees(rc.recovered_paise, true)} recovered</span></dd>
        <dt>Leaking now</dt><dd>${rc.weekly_leakage_paise ? rupees(rc.weekly_leakage_paise, true) + " a week" : "Stopped"}</dd>
        <dt>Future leakage</dt><dd>${rupees(rc.projected_leakage_paise)} over ${rc.horizon_weeks} weeks <span class="note">(${esc(rc.horizon_note)})</span></dd>
      </dl></div>`).join("");
  },

  async network() {
    const n = await (await fetch("/api/network")).json();
    const max = Math.max(1, ...n.patterns.map((p) => p.merchants_affected));
    return `<div class="privacy">${SHIELD}<div>${n.signatures.toLocaleString("en-IN")} signals from merchant teammates. No payments, ledgers or merchant names are shared, and a pattern stays hidden until at least ${n.k} merchants report it.</div></div>
      <table class="grid"><thead><tr><th>Pattern</th><th class="num">Merchants</th><th></th><th>Where it comes from</th><th class="num">Impact at least</th></tr></thead><tbody>` +
      n.patterns.map((p) => `<tr><td><div class="case-title">${esc(pattern(p.pattern))}</div><div class="case-sub">${instrument(p.instrument === "-" ? null : p.instrument)} · ${p.first_month} to ${p.last_month}</div></td>
        <td class="num amount">${p.merchants_affected}</td><td style="width:110px"><div class="bar"><span style="width:${(100 * p.merchants_affected / max).toFixed(0)}%"></span></div></td>
        <td>${Math.round(p.top_route_share * 100)}% via ${esc(p.top_route)}</td>
        <td class="num">${rupees(p.aggregate_impact_floor_paise)}</td></tr>`).join("") + `</tbody></table>`;
  },

  async redteam() {
    const r = state?.redteam;
    if (!r) return `<p class="empty">Not run yet. Step 10 fires legitimate charges built to look like violations, plus genuine issues, through the real system.</p>`;
    return `<div class="summary-strip">${stat("adversarial tests", r.generated)}${stat("correctly rejected", r.correctly_rejected)}${stat("sent for review", r.correctly_escalated)}${stat("genuine issues claimed", `${r.controls_claimed}/${r.controls}`)}${stat("false claims", r.false_claims, r.false_claims ? "" : "good")}</div>
      <table class="grid"><thead><tr><th>Test</th><th>Expected</th><th>Teammate did</th><th>Result</th></tr></thead><tbody>` +
      r.scenarios.map((sc) => `<tr><td>${esc(sc.explanation)}</td><td>${sc.expected.replaceAll("_", " ").toLowerCase()}</td><td>${sc.actual.replaceAll("_", " ").toLowerCase()}</td>
        <td><span class="pill ${sc.actual === sc.expected ? "pass" : "fail"}">${sc.actual === sc.expected ? "Correct" : "Wrong"}</span></td></tr>`).join("") + `</tbody></table>`;
  },

  async baseline() {
    const b = state?.baseline;
    if (!b) return `<p class="empty">Not run yet. Step 11 audits the same merchant's ledger with nothing wrong in it.</p>`;
    return `<p class="note">Same merchant, same ${b.lines.toLocaleString("en-IN")} settlement lines, with nothing wrong. A teammate that invents findings would show numbers here.</p>
      <div class="summary-strip">${stat("discrepancies proven", b.proven_cases, "good")}${stat("claims filed", b.claims_filed, "good")}${stat("recovered", rupees(b.recovered_paise), "good")}${stat("sent for review", b.escalated, "good")}${stat("batches reconciled", b.batches)}</div>`;
  },

  async message() {
    const step = state?.last_step?.key === "notify" ? state.last_step.data : null;
    if (!step) return `<p class="empty">The merchant hears from the teammate at the end of the story (step 12).</p>`;
    return `<div class="notif-wrap">
      <div class="phone"><div class="phone-screen">
        <div class="phone-time">9:41</div>
        <div class="notif">
          <div class="notif-head">${SHIELD}Paytm Business · Settlement Teammate<span class="when">now</span></div>
          <div class="notif-title">Settlement update</div>
          <div class="notif-text">${esc(step.text)}</div>
        </div>
      </div></div>
      <div><h3 style="margin:4px 0 8px;color:var(--navy)">What the merchant sees</h3>
        <p>One sentence in the language they use. No MDR tables, no settlement maths.</p>
        <p class="note">In English: ${esc(step.english)}</p>
        <p class="note">Delivered to ${esc(step.channel)} · ${step.language === "hi-en" ? "Hinglish" : esc(step.language)} · composed by ${esc(step.source)}</p></div>
    </div>`;
  },
};

// -- case drill-down --------------------------------------------------------------------------

function openDrawer(open) {
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("scrim").hidden = !open;
}

const CHECK = `<svg viewBox="0 0 24 24"><path d="M5 12.5l4.2 4.2L19 7" stroke="currentColor" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const PERSON = `<svg viewBox="0 0 24 24"><circle cx="12" cy="8.5" r="3.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.2 1.9 7 5.5" stroke="currentColor" stroke-width="2.2" fill="none"/></svg>`;

async function openCase(caseId) {
  if (!caseId) return;
  const d = await (await fetch(`/api/cases/${caseId}`)).json();
  const c = d.case, p = d.proof, cl = d.claim;
  const proven = p && p.verdict === "PROVEN";
  const verdictText = proven ? `Verified: ${rupees(p.discrepancy_paise, true)} owed to the merchant`
    : p && p.verdict === "UNPROVEN" ? "Not proven: sent for review, no claim filed" : "No discrepancy";
  const seal = p ? `<div class="seal ${proven ? "" : "unproven"}"><span class="seal-badge">${proven ? CHECK : PERSON}</span>
      <div><strong>${verdictText}</strong><small>${p.proof_id} · ${p.computed_at.slice(0, 10)} · ${p.records} records checked</small></div></div>` : "";

  const txRows = d.transactions.map((t) => t.device
    ? `<tr><td class="mono">${t.txn_id}</td><td colspan="3">Soundbox ${t.device.device_id}: activated ${t.device.activated_on}, rental-free until ${t.device.rental_free_until}, <b>returned ${t.device.returned_on || "not returned"}</b>${t.device.return_ref ? ` (pickup ref ${t.device.return_ref})` : ""}. Monthly rental ${rupees(t.amount_paise, true)}.</td></tr>`
    : `<tr><td class="mono">${t.txn_id}</td><td>${instrument(t.instrument)}</td><td class="num">${rupees(t.amount_paise, true)}</td><td>${t.captured_at.slice(0, 16).replace("T", " ")}</td></tr>`).join("");
  const lineRows = d.lines.map((l) => `<tr><td class="mono">${l.batch_id}</td><td class="num">${rupees(l.gross_paise, true)}</td><td class="num dr">${rupees(l.mdr_paise, true)}</td><td class="num dr">${rupees(l.gst_paise, true)}</td><td class="num dr">${rupees(l.tcs_paise + l.tds_paise, true)}</td><td class="num">${rupees(l.net_paise, true)}</td></tr>`).join("");
  const credits = d.credits.slice(0, 3).map((k) => `<div class="utr">Bank credit ${k.value_date} · UTR ${k.utr} · ${rupees(k.amount_paise, true)}</div>`).join("");
  const rules = d.rules.map((r) => `<div class="card" style="margin:6px 0"><b>${esc(r.name)}</b><div class="mono">${esc(r.rule_id)}</div><div class="note">${esc(r.source)}${r.effective_from ? ` · in force from ${r.effective_from}${r.effective_to ? " to " + r.effective_to : ""}` : ""} · ${esc(r.status).toLowerCase()} · ${esc(r.confidence).toLowerCase()} confidence</div></div>`).join("");
  const calc = p ? p.computation.map((sc) => `${sc.label}\n  ${sc.expression}${sc.result_paise != null ? "\n  = " + sc.result_paise + " paise" : ""}`).join("\n\n") : "";
  const followups = cl ? cl.steps.filter((sc) => ["SUBMIT", "FOLLOW_UP", "RESPONSE", "REPRESENT"].includes(sc.step))
    .map((sc) => `<div>${sc.at.slice(0, 10)} · ${sc.step.replaceAll("_", " ").toLowerCase()}${sc.status ? " · " + sc.status.toLowerCase().replaceAll("_", " ") : ""}${sc.reason_code ? " · " + sc.reason_code : ""}${sc.added ? " · attached " + sc.added.join(", ") : ""} <span class="note">(${esc(sc.engine)})</span></div>`).join("") : "";
  const decisions = d.history.filter((h) => ["PROVEN", "UNPROVEN", "ESCALATED", "ACTION_PENDING", "BATCHED", "CLOSED"].includes(h.to_state))
    .map((h) => `<div>${esc(STATE_LABELS[h.to_state] || h.to_state)}: ${esc(h.reason)}</div>`).join("");
  const step = (n, label, content) => `<li data-n="${n}"><div class="chain-card"><div class="chain-label">${label}</div>${content}</div></li>`;
  const outcome = c.recovered_paise ? `<b class="cr">${rupees(c.recovered_paise, true)} back in the merchant's account</b>` : chip(c.state);
  const rootCause = d.root_cause ? `<div class="note" style="margin-top:6px">Root cause: ${esc(d.root_cause.cause)}. Fix: ${esc(d.root_cause.prevention_action)}</div>` : "";

  $("drawer-body").innerHTML = `
    <div class="case-hero">
      <div class="eyebrow">${c.case_id} · ${c.month}</div>
      <h2>${esc(pattern(c.pattern))} · ${instrument(c.instrument)}</h2>
      <div>${chip(c.state)} <span class="note">&nbsp;${c.txn_count} payments · opened ${c.opened_at.slice(0, 10)}</span></div>
    </div>
    <ol class="chain">
      ${step(1, "Payments", `<table class="grid"><tbody>${txRows}</tbody></table>${c.txn_count > d.transactions.length ? `<p class="note">and ${c.txn_count - d.transactions.length} more</p>` : ""}`)}
      ${step(2, "Settlement", `<table class="grid"><thead><tr><th>Batch</th><th class="num">Gross</th><th class="num">MDR</th><th class="num">GST</th><th class="num">TCS+TDS</th><th class="num">Net</th></tr></thead><tbody>${lineRows || `<tr><td colspan="6">No settlement line exists for these payments.</td></tr>`}</tbody></table>${credits}`)}
      ${step(3, "Detection", `<div>${esc(c.rationale || "")}</div><div class="note">Reasoning: ${esc(c.reasoner || "—")}. Interpretation only; it never sets the amount.</div>`)}
      ${step(4, "Rule", rules)}
      ${step(5, "Calculation", `<div class="calc">${esc(calc)}</div>`)}
      ${step(6, "Proof", p ? `<div class="figures"><div class="figure"><small>Expected</small><b>${rupees(p.expected_paise, true)}</b></div><div class="figure"><small>Actual</small><b>${rupees(p.actual_paise, true)}</b></div><div class="figure hl"><small>Verified difference</small><b>${rupees(p.discrepancy_paise, true)}</b></div></div>${seal}${p.unproven_reason ? `<p class="note">${esc(p.unproven_reason)}</p>` : ""}` : "Not yet proven.")}
      ${step(7, "Decision", decisions || "—")}
      ${step(8, "Correction", cl ? `<div><span class="mono">${cl.claim_id}</span> · ref <span class="mono">${cl.reference}</span> · <b>${rupees(cl.amount_paise, true)}</b> sent to Paytm settlement ops via the ${esc(cl.workflow)} workflow</div><div class="note">Evidence attached: ${cl.attachments.join(", ")}${c.human_attestation ? ` · filed on the authority of ${esc(c.human_attestation.reviewer)}` : ""}</div>` : "Nothing filed.")}
      ${step(9, "Follow-up", followups || "—")}
      ${step(10, "Outcome", outcome + rootCause)}
    </ol>`;
  openDrawer(true);
  $("drawer-body").scrollTop = 0;
  $("drawer-close").focus();
}

$("drawer-close").addEventListener("click", () => openDrawer(false));
$("scrim").addEventListener("click", () => openDrawer(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") openDrawer(false); });

// -- controls ----------------------------------------------------------------------------------

let busy = false;
let autoplay = false;

async function review(caseId, action) {
  const verb = action === "file" ? "File this correction on your authority" : "Dismiss this case";
  const note = window.prompt(`${verb}. Add a note for the record (optional):`, "");
  if (note === null) return;
  const res = await fetch(`/api/cases/${caseId}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, note, reviewer: "Merchant success desk" }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(body.detail || "The review could not be recorded.");
  }
  await refreshAll();
}

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
    if (err instanceof TypeError) setOffline(true);  // network failure: the banner explains and retries
    else alert(err.message);
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
  if (key === "investigation") showcaseId = body.step.data.case?.case_id;
  if (key === "investigation" || key === "proof") openCase(showcaseId);
  else openDrawer(false);
  const tabFor = { network: "network", red_team: "redteam", baseline: "baseline", notify: "message", recovery: "causes", backfill: "cases" }[key];
  if (tabFor) selectTab(tabFor);
});
$("btn-advance").addEventListener("click", () => act("/api/clock/advance?days=1"));
$("btn-chat").addEventListener("click", () => {
  window.open("/chat", "merchant-chat", "width=440,height=880");
});

$("btn-reset").addEventListener("click", async () => {
  autoplay = false;
  $("btn-auto").setAttribute("aria-pressed", "false");
  eventCursor = 0; feedItems.length = 0; showcaseId = null; $("feed").innerHTML = "";
  openDrawer(false);
  await act("/api/demo/reset");
  selectTab("cases");
});
$("btn-auto").addEventListener("click", async () => {
  autoplay = !autoplay;
  $("btn-auto").setAttribute("aria-pressed", String(autoplay));
  $("btn-auto").textContent = autoplay ? "Pause story" : "Play the story";
  while (autoplay && !state.steps.every((st) => st.done)) {
    $("btn-next").click();
    await new Promise((r) => setTimeout(r, 400));
    while (busy) await new Promise((r) => setTimeout(r, 200));
    await new Promise((r) => setTimeout(r, 3200));
  }
  autoplay = false;
  $("btn-auto").setAttribute("aria-pressed", "false");
  $("btn-auto").textContent = "Play the story";
});

refreshAll();
