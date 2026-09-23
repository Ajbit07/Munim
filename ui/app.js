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
  FOLLOWUP_AGENT: "Follow-up agent", WORKFLOW_ENGINE: "Claim workflow", SYSTEM: "System", HUMAN: "Person",
};
const INITIALS = { MONITOR_AGENT: "MO", INVESTIGATION_AGENT: "IN", PROOF_ENGINE: "PR", FOLLOWUP_AGENT: "FU", WORKFLOW_ENGINE: "WF", SYSTEM: "SY", HUMAN: "PE" };
const STATE_LABELS = {
  RECOVERED: "Recovered", PARTIALLY_RECOVERED: "Partly recovered", ESCALATED: "Awaiting decision", CLOSED_UNRECOVERED: "Not recovered",
  WAITING: "Awaiting ops", AWAITING_CREDIT: "Approved, awaiting credit", FILED: "Correction filed", FOLLOW_UP: "Following up", REPRESENT: "Re-presenting", REJECTED: "Rejected",
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

const SHOW_STATES = new Set(["AWAITING_CREDIT", "FILED", "PARTIALLY_RECOVERED", "ESCALATED", "REJECTED", "REPRESENT", "CLOSED_UNRECOVERED", "FOLLOW_UP", "WITHDRAWN", "CLOSED"]);

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
    case "ticket.opened": return [`Merchant reported via chat: “${p.statement}”. Records showed nothing yet, so ticket ${p.ticket_id} went to the team.`, "alert"];
    case "ticket.resolved": return [`Ticket ${p.ticket_id} answered by ${p.resolved_by}; the reply went to the merchant's chat.`];
    case "case.appealed": return [`${c}merchant appealed: “${p.reason}”. Waiting for an ops decision.`, "alert"];
    case "workflow.n8n.step": return [`n8n executed the ${p.action.replaceAll("_", "-")} step for ${p.claim_id}.`];
    case "pattern.recurred": return [`${c}the fix confirmed earlier did not hold: ${pattern(p.pattern).toLowerCase()} is back. Correction re-requested.`, "alert"];
    case "workflow.fallback": return [`n8n unreachable; the claim continues on the local workflow.`, "alert"];
    case "recovery.confirmed": return [p.utr
      ? `${rupees(p.recovered_paise, true)} credited to the merchant's bank in ${p.batch_id} on ${niceDate(p.settlement_date)} (UTR ${p.utr}). Verified against the approved amount.`
      : `${rupees(p.recovered_paise, true)} back in the merchant's account (${p.claim_id}).`, "money"];
    case "payout.awaiting": return [`${c}approved by settlement ops for ${rupees(p.approved_paise, true)}. Not counted as recovered until the credit reaches the bank.`];
    case "payout.chased": return [`${c}approved ${p.days_waited} days ago but not yet credited. Payout chased (${p.chase}).`, "alert"];
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
  status.innerHTML = `<span class="dot"></span>${merchant.connected ? "Munim is watching" : "Not under watch yet"}`;
  $("today").textContent = niceDate(s.today);
  $("complaints").textContent = m.open_complaints;

  $("hero-found").textContent = rupees(m.identified_paise);
  $("hero-back").textContent = rupees(m.recovered_paise);
  const pct = m.identified_paise ? Math.round((100 * m.recovered_paise) / m.identified_paise) : 0;
  $("hero-bar").style.width = `${pct}%`;
  $("hero-tick").classList.toggle("on", m.recovered_paise > 0);  // Munim ticks the entry once money is back
  $("hero-bar-wrap").setAttribute("aria-valuenow", String(pct));
  if (m.identified_paise > 0) {
    refreshPortfolio();
  $("thesis-line").textContent = `${m.proven_cases} proven cases across ${m.months_affected} months · ${pct}% recovered so far`;
  } else if (merchant.connected) {
    $("thesis-line").textContent = "Audit complete. Every settlement reconciles.";
  } else {
    $("thesis-line").textContent = "Munim has not opened this bahi yet.";
  }

  $("m-identified").textContent = rupees(m.identified_paise);
  $("m-recovered").textContent = rupees(m.recovered_paise);
  $("m-prevented").textContent = rupees(m.future_leakage_prevented_paise);
  $("m-progress").textContent = rupees(m.in_progress_paise);
  $("m-proven").textContent = m.proven_cases;
  $("m-active").textContent = m.active_claims;
  $("m-escalated").textContent = m.escalated;
  $("m-escalated-note").textContent = m.human_filed_claims ? `${m.human_filed_claims} filed on a person's authority` : "";
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
  $("btn-next").textContent = finished ? "Tour complete" : "Run next step";
  $("btn-advance").disabled = !merchant.connected;
  $("btn-live").disabled = !merchant.connected;
  renderLive(s);
}

// -- portfolio (ops) ----------------------------------------------------------------------------

let portfolioBusy = false;

async function refreshPortfolio() {
  if (portfolioBusy) return;
  portfolioBusy = true;
  try {
    const p = await (await fetch("/api/portfolio")).json();
    $("pf-monitored").textContent = `${p.merchants_monitored} of ${p.merchants_total} merchants under watch`;
    $("pf-found").textContent = rupees(p.identified_paise);
    $("pf-returned").textContent = rupees(p.recovered_paise);
    $("pf-progress").textContent = rupees(p.in_progress_paise);
    $("pf-decisions").textContent = p.awaiting_decision;
    $("pf-tickets").textContent = p.open_tickets;
    $("pf-complaints").textContent = p.merchant_complaints;
  } catch (_) { /* the offline banner covers this */ } finally {
    portfolioBusy = false;
  }
}

// -- live operation ----------------------------------------------------------------------------

let merchantsLoaded = null;
let liveTimer = null;
let pendingMerchant = null;  // picked in the dropdown but not connected yet

async function loadMerchants(force = false) {
  if (merchantsLoaded && !force) return;
  const list = await (await fetch("/api/merchants")).json();
  merchantsLoaded = list;
  $("merchant-select").innerHTML = list.map((m) =>
    `<option value="${m.merchant_id}">${esc(m.legal_name)} · ${esc(m.city)}${m.connected ? " ✓" : ""}</option>`).join("");
}

function renderLive(s) {
  const m = s.merchant;
  $("ds-seed").textContent = s.dataset.uploaded ? `${s.dataset.name} (uploaded Paytm report)` : `seed ${s.dataset.seed}`;
  $("ds-merchants").textContent = s.dataset.merchants;
  $("ds-through").textContent = niceDate(s.dataset.data_through);
  $("live-clock").textContent = `${niceDate(s.today)} · ${liveTimer ? "running: one day every 4 seconds" : "paused"}`;
  if (pendingMerchant) return;  // leave the user's pick alone until they connect it
  if (merchantsLoaded) {
    $("merchant-select").value = m.merchant_id;
    const current = merchantsLoaded.find((x) => x.merchant_id === m.merchant_id);
    if (current && current.connected !== m.connected) loadMerchants(true);
  }
  $("btn-connect").textContent = m.connected ? "Connected ✓" : "Connect merchant";
  $("btn-connect").disabled = m.connected;
  if (!m.connected) {
    $("live-title").textContent = `${m.legal_name} is not connected`;
    $("live-narrative").textContent = "Connecting starts the Monitor agent on twelve months of this merchant's settlements. Pick any merchant: each has a different ledger and different problems.";
  } else {
    const x = s.metrics;
    $("live-title").textContent = x.identified_paise ? `${x.proven_cases} proven cases for ${m.legal_name}` : `${m.legal_name}: every settlement reconciles`;
    $("live-narrative").textContent = liveTimer
      ? "Time is running. New settlements arrive, the Monitor reconciles them, and the Follow-up agent chases open corrections, all without anyone pressing a button."
      : "Press Run live to let time pass: the agents keep working on their own. Open any case to see its proof.";
  }
}

function setLive(on) {
  if (on && !liveTimer) {
    liveTimer = setInterval(async () => {
      if (busy) return;
      await act("/api/clock/advance?days=1", { quiet: true });
    }, 4000);
  } else if (!on && liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
  $("btn-live").setAttribute("aria-pressed", String(on));
  $("btn-live").textContent = on ? "Pause" : "Run live";
  if (state) renderLive(state);
}

$("btn-live").addEventListener("click", () => setLive(!liveTimer));

$("merchant-select").addEventListener("change", async () => {
  const id = $("merchant-select").value;
  const known = merchantsLoaded?.find((x) => x.merchant_id === id);
  if (known?.connected) {
    pendingMerchant = null;
    await act("/api/live/select", { body: { merchant_id: id } });
    openDrawer(false);
  } else {
    // Show who is selected; connecting is the explicit, visible act that starts the agents.
    pendingMerchant = id;
    $("btn-connect").textContent = "Connect merchant";
    $("btn-connect").disabled = false;
    $("live-title").textContent = `${known?.legal_name || id} is not connected`;
  }
});

$("btn-connect").addEventListener("click", async () => {
  const id = $("merchant-select").value;
  pendingMerchant = null;
  await act("/api/live/select", { body: { merchant_id: id } });
  await loadMerchants(true);
  openDrawer(false);
  selectTab("cases");
});

// -- fresh datasets ------------------------------------------------------------------------------

let datasetPoll = null;

async function refreshDatasets() {
  const d = await (await fetch("/api/datasets")).json();
  const g = d.generation || {};
  const el = $("ds-status");
  if (g.state === "generating") {
    el.textContent = `Generating seed ${g.seed}: 25 merchants, 12 months, plus a clean copy…`;
    $("btn-dataset").disabled = true;
  } else if (g.state === "ready" && g.seed !== d.current) {
    el.innerHTML = `Seed ${g.seed} generated in ${g.seconds}s. <button class="btn btn-solid btn-sm" id="btn-use-seed">Switch to it</button>`;
    $("btn-use-seed").addEventListener("click", () => useSeed(g.seed));
    $("btn-dataset").disabled = false;
  } else if (g.state === "failed") {
    el.textContent = `Generation failed: ${g.error}`;
    $("btn-dataset").disabled = false;
  } else {
    el.textContent = "";
    $("btn-dataset").disabled = false;
  }
  if (g.state !== "generating" && datasetPoll) { clearInterval(datasetPoll); datasetPoll = null; }
}

async function useSeed(seed) {
  setLive(false);
  eventCursor = 0; feedItems.length = 0; showcaseId = null; $("feed").innerHTML = "";
  openDrawer(false);
  await act("/api/datasets/use", { body: { seed } });
  await loadMerchants(true);
  await refreshDatasets();
  selectTab("cases");
}

$("btn-dataset").addEventListener("click", async () => {
  const res = await fetch("/api/datasets/new", { method: "POST" });
  if (!res.ok) { toast((await res.json()).detail || "Could not start generation"); return; }
  await refreshDatasets();
  datasetPoll = setInterval(refreshDatasets, 2000);
});

// -- importing a real Paytm settlement report -----------------------------------------------------

function bindUpload() {
  const status = (t) => { $("up-status").textContent = t; };
  const fields = ["up-name", "up-city", "up-mcc", "up-credit", "up-debit", "up-nb", "up-turnover", "up-sla", "up-eco"];
  const syncOverride = () => fields.forEach((id) => { $(id).disabled = !$("up-override").checked; });
  $("up-override").addEventListener("change", syncOverride);
  syncOverride();
  const run = async (csv) => {
    const override = $("up-override").checked;
    const profile = {
      legal_name: $("up-name").value, city: $("up-city").value, mcc: $("up-mcc").value, credit: $("up-credit").value,
      debit: $("up-debit").value, netbanking: $("up-nb").value, turnover_lakh: $("up-turnover").value,
      sla_days: $("up-sla").value, ecommerce: $("up-eco").checked,
    };
    setLive(false);
    status(`Importing ${Math.round(csv.length / 1024)} KB and auditing…`);
    $("up-go").disabled = $("up-sample").disabled = true;
    try {
      const res = await fetch("/api/upload", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csv, profile, override }),
      });
      const r = await res.json();
      if (!res.ok) throw new Error(r.detail || "Import failed");
      const i = r.import;
      const aside = Object.entries(i.set_aside).map(([k, v]) => `<li>${v.toLocaleString("en-IN")} × ${esc(k)}</li>`).join("");
      const m = r.state.metrics;
      const who = r.merchant
        ? `<p class="note"><b>Merchant found in the records:</b> ${esc(r.merchant.legal_name)} (${esc(r.merchant.merchant_id)}), ${esc(r.merchant.city)},
           MCC ${esc(r.merchant.mcc)}, matched by ${esc(r.merchant.matched_by)}. Agreement: credit ${esc(r.merchant.card_credit_rate_percent)}%,
           debit ${esc(r.merchant.card_debit_rate_percent)}%, netbanking ${esc(r.merchant.netbanking_rate_percent)}%
           (${r.merchant.agreements} agreement version${r.merchant.agreements === 1 ? "" : "s"}); ${r.merchant.devices} rented device(s); turnover
           ${rupees(r.merchant.annual_turnover_paise)}.</p>`
        : `<p class="note"><b>Merchant not found in the records:</b> audited with the details entered on the form${i.turnover_estimated ? "; last year's turnover estimated from the report" : ""}.</p>`;
      $("up-result").innerHTML = `<div class="summary-strip">${stat("rows read", i.rows_read.toLocaleString("en-IN"))}${stat("payments", i.payments.toLocaleString("en-IN"))}${stat("refunds", i.refunds)}${stat("payouts (UTRs)", i.payouts)}${stat("found in error", rupees(m.identified_paise), "good")}${stat("proven cases", m.proven_cases, "good")}</div>
        ${who}
        <p class="note">${esc(i.first_date)} to ${esc(i.last_date)}. ${i.rentals ? `${i.rentals} rental debits checked against device records. ` : ""}Columns recognised: ${i.columns_found.map(esc).join(", ")}.</p>
        ${aside ? `<p class="note">Set aside, not guessed:</p><ul class="note">${aside}</ul>` : ""}
        <p class="note">The audit has run. Open the <b>Cases</b> tab, or the merchant's app view, as for any merchant.</p>`;
      status("Done.");
      eventCursor = 0; feedItems.length = 0; $("feed").innerHTML = "";
      await loadMerchants(true);
      await refreshAll();
    } catch (err) {
      status("");
      $("up-result").innerHTML = `<p class="review-error">${esc(err.message)}</p>`;
    } finally {
      $("up-go").disabled = $("up-sample").disabled = false;
    }
  };
  $("up-go").addEventListener("click", async () => {
    const file = $("up-file").files[0];
    if (!file) { status("Choose a CSV file first."); return; }
    run(await file.text());
  });
  $("up-sample").addEventListener("click", async () => {
    status("Loading the sample report…");
    const res = await fetch("/api/upload/sample");
    if (!res.ok) { status("No sample report on the server. Run: python tools/export_paytm_report.py"); return; }
    $("up-override").checked = false;
    syncOverride();
    run(await res.text());
  });
}

// -- guided tour (the scripted walk-through, for presenting) --------------------------------------

function setTour(on) {
  document.body.classList.toggle("tour", on);
  if (on) setLive(false);
}
$("btn-tour").addEventListener("click", () => setTour(!document.body.classList.contains("tour")));
$("btn-exit-tour").addEventListener("click", () => setTour(false));

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
  if (tab === "upload" && el.dataset.rendered) return;  // a form: never wipe what ops is typing
  if (tab === "upload") el.dataset.rendered = "1";
  const html = await render();
  if (token !== renderToken) return;  // a newer render started; drop this stale one
  el.innerHTML = html;
  el.querySelectorAll("[data-case]").forEach((row) => {
    row.addEventListener("click", () => openCase(row.dataset.case));
    row.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && ev.target === row) openCase(row.dataset.case); });
  });
  if (tab === "upload") bindUpload();
  el.querySelectorAll("[data-merchant]").forEach((row) => row.addEventListener("click", async () => {
    pendingMerchant = null;
    await act("/api/live/select", { body: { merchant_id: row.dataset.merchant } });
    await loadMerchants(true);
    openDrawer(false);
    selectTab("cases");
  }));
  el.querySelectorAll("[data-resolve]").forEach((btn) => btn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const text = btn.parentElement.querySelector("textarea")?.value.trim();
    if (!text) { toast("Write the reply the merchant will see first."); return; }
    btn.disabled = true;
    await fetch(`/api/tickets/${btn.dataset.resolve}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolved_by: "Settlement ops desk", resolution: text }),
    });
    await refreshAll();
  }));
  el.querySelectorAll("[data-run]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Running…";
    await act(btn.dataset.run);
  }));
  el.querySelectorAll("[data-review]").forEach((btn) => btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    askReview(btn);
  }));
}

const SHIELD = `<svg viewBox="0 0 32 32" width="20" height="20"><path d="M16 3l11 4v8c0 7-4.7 12.3-11 14.5C9.7 27.3 5 22 5 15V7z" fill="#00BAF2"/><path d="M11 15.8l3.4 3.4 6.6-6.8" stroke="#fff" stroke-width="2.8" fill="none" stroke-linecap="round"/></svg>`;

const MCCS = [["5411", "Grocery / supermarket"], ["5499", "Food store"], ["5812", "Restaurant"], ["5912", "Pharmacy"],
  ["5541", "Fuel station"], ["5542", "Fuel (automated)"], ["5999", "Other retail"], ["8220", "Education"],
  ["4812", "Telecom"], ["7230", "Salon"]];

const TABS = {
  async upload() {
    return `<p class="note">Upload a settlement report downloaded from the Paytm merchant dashboard (Reports → Settlements), as CSV.
      The importer reads Paytm's columns (Transaction ID, Order ID, Transaction Date, Transaction Type, Status, Amount,
      Commission, GST, Settled Amount, Settled Date, UTR No., Payment Mode) and the API's camelCase names. The merchant, its
      agreed rates and amendments, category, turnover and devices are looked up automatically in the merchant records
      (by MID, or by recognising the report's transaction IDs). Nothing needs typing. The same engines then audit it, live.</p>
      <form class="upload-form" id="upload-form">
        <label class="full">Settlement report (CSV)<input type="file" id="up-file" accept=".csv,text/csv"></label>
        <label class="check full"><input type="checkbox" id="up-override"> The merchant is not in Paytm's records: enter their details below instead</label>
        <label>Merchant name<input id="up-name" value="Uploaded merchant"></label>
        <label>City<input id="up-city" value="Mumbai"></label>
        <label>Business category (MCC)<select id="up-mcc">${MCCS.map(([c, n]) => `<option value="${c}">${c} · ${n}</option>`).join("")}</select></label>
        <label>Agreed credit card rate (%)<input id="up-credit" value="1.80" inputmode="decimal"></label>
        <label>Agreed debit card rate (%)<input id="up-debit" value="0.40" inputmode="decimal"></label>
        <label>Agreed netbanking rate (%)<input id="up-nb" value="1.50" inputmode="decimal"></label>
        <label>Last year's turnover (₹ lakh)<input id="up-turnover" value="" placeholder="blank: estimated from the report" inputmode="decimal"></label>
        <label>Settlement timeline (T + days)<input id="up-sla" value="1" inputmode="numeric"></label>
        <label class="check"><input type="checkbox" id="up-eco"> Sells through an e-commerce operator</label>
      </form>
      <div class="upload-actions">
        <button class="btn btn-solid" id="up-go">Import and audit</button>
        <button class="btn btn-outline" id="up-sample" title="A report in Paytm's format exported from the demo data">Use the sample report</button>
        <span class="muted" id="up-status"></span>
      </div>
      <div class="upload-result" id="up-result"></div>`;
  },

  async merchants() {
    const p = await (await fetch("/api/portfolio")).json();
    const rows = p.merchants.map((m) => `<tr class="clickable ${m.merchant_id === p.focus ? "focus" : ""}" data-merchant="${m.merchant_id}" tabindex="0">
        <td><b>${esc(m.legal_name)}</b><div class="muted">${esc(m.city)} · MCC ${esc(m.mcc)} · ${esc(m.acquirer)}</div></td>
        <td>${m.connected ? `<span class="pill RECOVERED">Under watch</span>` : `<span class="pill">Not connected</span>`}</td>
        <td class="num amount">${m.connected ? rupees(m.identified_paise) : "—"}</td>
        <td class="num">${m.connected ? rupees(m.recovered_paise) : "—"}</td>
        <td class="num">${m.connected ? (m.awaiting_decision || "—") : "—"}</td>
        <td class="num">${m.connected ? (m.open_tickets || "—") : "—"}</td>
        <td class="num">${m.connected ? (m.late_payments || "—") : "—"}</td></tr>`).join("");
    return `<div class="summary-strip">${stat("merchants under watch", `${p.merchants_monitored}/${p.merchants_total}`)}${stat("awaiting a decision", p.awaiting_decision)}${stat("open merchant tickets", p.open_tickets)}</div>
      ${p.merchants_monitored < p.merchants_total ? `<button class="btn btn-solid" data-run="/api/live/connect-all">Put all ${p.merchants_total} merchants under watch</button>` : ""}
      <table class="grid" style="margin-top:12px"><thead><tr><th>Merchant</th><th>Status</th><th class="num">Found</th><th class="num">Returned</th><th class="num">Decisions</th><th class="num">Tickets</th><th class="num">Late payments</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="note">Click a merchant to open it. Connecting one starts the agents on its twelve months of settlements.</p>`;
  },

  async impact() {
    const res = await fetch("/api/impact");
    if (res.status === 409) return `<p class="empty">Run the audit first (step 3). The comparison is computed from its results.</p>`;
    const d = await res.json();
    const n = (x) => Number(x).toLocaleString("en-IN");
    const leakBeyond = d.projected_leakage_paise;
    return `<p class="note">Same merchant, same twelve months. Every figure below is computed from the audit; nothing is estimated by hand.</p>
      <div class="impact">
        <section class="impact-col without">
          <h3>Without Munim</h3>
          <div class="impact-big">${rupees(d.identified_paise)}</div>
          <p>deducted in error and never noticed</p>
          <ul>
            <li>The merchant would have to spot <b>${n(d.cases)} separate problems</b> across ${n(d.months)} months of statements, and raise a complaint for each.</li>
            <li><b>${n(d.lines_checked)}</b> settlement lines to check by hand, across ${n(d.batches_checked)} settlement batches.</li>
            <li>The causes keep charging: about <b>${rupees(leakBeyond)}</b> more in the coming months.</li>
            <li>${n(d.late_payments)} late payments (${rupees(d.late_paise)}) go unreported.</li>
          </ul>
        </section>
        <section class="impact-col with">
          <h3>With Munim</h3>
          <div class="impact-big">${rupees(d.recovered_paise)}</div>
          <p>already back in the merchant's account</p>
          <ul>
            <li><b>${n(d.complaints_raised)}</b> complaints raised by the merchant. Paytm found and corrected its own errors.</li>
            <li>Every line checked automatically, each correction backed by a recomputed proof.</li>
            <li>Causes fixed at the source: <b>${rupees(d.future_leakage_prevented_paise)}</b> of future wrong charges stopped.</li>
            <li>${n(d.escalated)} unclear case(s) (${rupees(d.escalated_paise)}) held for a person instead of guessed.</li>
            ${d.false_claims === null ? "" : `<li><b>${n(d.false_claims)}</b> false corrections in the red-team test.</li>`}
          </ul>
        </section>
      </div>`;
  },

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
    const [items, tickets] = await Promise.all([
      fetch("/api/human-queue").then((r) => r.json()), fetch("/api/tickets").then((r) => r.json())]);
    const kind = { handoff: "Merchant asked for a person", appeal: "Merchant appeal" };
    const ticketHtml = tickets.map((t) => `<div class="card ticket">
      <div class="card-row"><h3>${esc(kind[t.topic] || "Reported by the merchant via chat")} · ${esc(t.ticket_id)}</h3>
        <span class="pill ${t.status === "OPEN" ? "NEEDS_HUMAN" : "RECOVERED"}">${t.status === "OPEN" ? "Open ticket" : "Resolved"}</span></div>
      <dl class="kv">
        <dt>Merchant said</dt><dd>“${esc(t.statement)}”</dd>
        <dt>Records showed</dt><dd>${esc(t.records_showed)}</dd>
        ${t.transcript?.length ? `<dt>Conversation</dt><dd>${t.transcript.map((x) => `<div><b>${x.role === "assistant" ? "Assistant" : "Merchant"}:</b> ${esc(x.content)}</div>`).join("")}</dd>` : ""}
        ${t.resolution ? `<dt>Reply sent</dt><dd>${esc(t.resolution)} (${esc(t.resolved_by)})</dd>` : ""}
      </dl>
      ${t.status === "OPEN" ? `<div class="reply-box"><textarea rows="2" maxlength="500" placeholder="Reply to the merchant (they see it in their chat)"></textarea>
        <button class="btn btn-solid" data-resolve="${esc(t.ticket_id)}">Send reply and resolve</button></div>` : ""}
    </div>`).join("");
    if (!items.length && !tickets.length) return `<p class="empty">Nothing is waiting on a person for this merchant. Cases the teammate cannot prove land here instead of being filed.</p>`;
    if (!items.length) return ticketHtml;
    return ticketHtml + `<p class="note">The teammate did not file these. It could not prove them from published rules and the merchant's records, so it is asking a person. The merchant can decide these in their app too; whoever decides first is recorded by name, and the teammate then follows it through.</p>` +
      items.map((c) => `<div class="card" data-case="${c.case_id}" tabindex="0" style="cursor:pointer">
      <div class="card-row"><h3>${esc(pattern(c.pattern))} · ${c.month}</h3><span class="pill NEEDS_HUMAN">${c.escalation?.appeal ? "Merchant appeal" : "Not filed"}</span></div>
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
    if (!r) return `<p class="empty">Not run yet. The red team generates legitimate charges built to look like violations, plus genuine issues, and runs them through the real system.</p>
      <button class="btn btn-solid" data-run="/api/redteam/run">Run the red team now</button>`;
    return `<div class="summary-strip">${stat("adversarial tests", r.generated)}${stat("correctly rejected", r.correctly_rejected)}${stat("sent for review", r.correctly_escalated)}${stat("genuine issues claimed", `${r.controls_claimed}/${r.controls}`)}${stat("false claims", r.false_claims, r.false_claims ? "" : "good")}</div>
      <table class="grid"><thead><tr><th>Test</th><th>Expected</th><th>Teammate did</th><th>Result</th></tr></thead><tbody>` +
      r.scenarios.map((sc) => `<tr><td>${esc(sc.explanation)}</td><td>${sc.expected.replaceAll("_", " ").toLowerCase()}</td><td>${sc.actual.replaceAll("_", " ").toLowerCase()}</td>
        <td><span class="pill ${sc.actual === sc.expected ? "pass" : "fail"}">${sc.actual === sc.expected ? "Correct" : "Wrong"}</span></td></tr>`).join("") + `</tbody></table>`;
  },

  async baseline() {
    const b = state?.baseline;
    if (!b) return `<p class="empty">Not run yet. This audits the same merchant's ledger with nothing wrong planted in it.</p>
      <button class="btn btn-solid" data-run="/api/baseline/run">Run the clean baseline</button>`;
    return `<p class="note">Same merchant, same ${b.lines.toLocaleString("en-IN")} settlement lines, with nothing wrong. A teammate that invents findings would show numbers here.</p>
      <div class="summary-strip">${stat("discrepancies proven", b.proven_cases, "good")}${stat("claims filed", b.claims_filed, "good")}${stat("recovered", rupees(b.recovered_paise), "good")}${stat("sent for review", b.escalated, "good")}${stat("batches reconciled", b.batches)}</div>`;
  },

  async message() {
    const step = state?.notification;
    if (!step) return state?.merchant.connected
      ? `<p class="empty">Compose the one-sentence update the merchant receives.</p>
         <button class="btn btn-solid" data-run="/api/notify">Compose merchant update</button>`
      : `<p class="empty">Connect a merchant first.</p>`;
    return `<div class="notif-wrap">
      <div class="phone"><div class="phone-screen">
        <div class="phone-time">9:41</div>
        <div class="notif">
          <div class="notif-head">${SHIELD}Paytm Business · Munim<span class="when">now</span></div>
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
  const credit = d.refund_credit;
  const outcome = credit
    ? `<b class="cr">${rupees(c.recovered_paise, true)} back in the merchant's account</b>
       <table class="grid" style="margin-top:8px"><tbody>
         <tr><td>Settlement line</td><td class="mono">${esc(credit.narration)}</td></tr>
         <tr><td>Batch</td><td class="mono">${esc(credit.batch_id)}</td></tr>
         <tr><td>Credited on</td><td>${niceDate(credit.settlement_date)}</td></tr>
         <tr><td>Bank UTR</td><td class="mono">${esc(credit.utr)}</td></tr>
       </tbody></table>
       <div class="note">Counted as recovered only after this credit appeared, matched by reference and amount.</div>`
    : c.state === "AWAITING_CREDIT"
      ? `${chip(c.state)} <div class="note">Settlement ops approved ${rupees(d.approved_paise, true)}. The teammate checks incoming credits daily and chases if it is late.</div>`
      : c.recovered_paise ? `<b class="cr">${rupees(c.recovered_paise, true)} back in the merchant's account</b>` : chip(c.state);
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

// Inline confirmation: the browser pane does not support window.prompt/alert.
function askReview(btn) {
  const caseId = btn.dataset.rcase;
  const action = btn.dataset.review;
  const box = btn.closest(".actions");
  const original = box.innerHTML;
  const question = action === "file"
    ? "File this correction on the ops desk's authority? The reviewer's name is recorded as the approver."
    : "Dismiss this case? Nothing will be filed.";
  box.innerHTML = `<div class="review-confirm">
      <p>${question}</p>
      <input type="text" maxlength="300" placeholder="Note for the record (optional)" aria-label="Note for the record">
      <div class="review-buttons">
        <button type="button" class="btn ${action === "file" ? "btn-solid" : "btn-danger"}" data-confirm>${action === "file" ? "Confirm and file" : "Confirm dismiss"}</button>
        <button type="button" class="btn btn-outline" data-cancel>Cancel</button>
      </div>
      <p class="review-error" hidden></p>
    </div>`;
  box.addEventListener("click", (ev) => ev.stopPropagation());
  box.addEventListener("keydown", (ev) => ev.stopPropagation());
  const input = box.querySelector("input");
  input.focus();
  box.querySelector("[data-cancel]").addEventListener("click", () => {
    box.innerHTML = original;
    box.querySelectorAll("[data-review]").forEach((b) => b.addEventListener("click", (ev) => { ev.stopPropagation(); askReview(b); }));
  });
  const confirm = box.querySelector("[data-confirm]");
  const submit = async () => {
    confirm.disabled = true;
    confirm.textContent = "Recording…";
    const error = box.querySelector(".review-error");
    try {
      const res = await fetch(`/api/cases/${caseId}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: input.value.trim(), reviewer: "Settlement ops desk" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `The review could not be recorded (${res.status}).`);
      }
      await refreshAll();
      if (action === "file") openCase(caseId);
    } catch (err) {
      error.textContent = err instanceof TypeError ? "Can't reach the server. Try again." : err.message;
      error.hidden = false;
      confirm.disabled = false;
      confirm.textContent = "Try again";
    }
  };
  confirm.addEventListener("click", submit);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit(); });
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 6000);
}

async function act(url, { body = null, quiet = false } = {}) {
  if (busy) return null;
  busy = true;
  $("busy").hidden = false;
  if (!quiet) document.querySelectorAll(".controls .btn").forEach((b) => { if (!["btn-auto", "btn-live"].includes(b.id)) b.disabled = true; });
  const poll = setInterval(pullEvents, 600);
  try {
    const res = await fetch(url, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : { method: "POST" });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || res.statusText);
    return payload;
  } catch (err) {
    if (err instanceof TypeError) setOffline(true);  // network failure: the banner explains and retries
    else { toast(err.message); if (quiet) setLive(false); }
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
  setLive(false);
  $("btn-auto").setAttribute("aria-pressed", "false");
  eventCursor = 0; feedItems.length = 0; showcaseId = null; $("feed").innerHTML = "";
  openDrawer(false);
  await act("/api/demo/reset");
  selectTab("cases");
});
$("btn-auto").addEventListener("click", async () => {
  autoplay = !autoplay;
  $("btn-auto").setAttribute("aria-pressed", String(autoplay));
  $("btn-auto").textContent = autoplay ? "Pause tour" : "Play the tour";
  while (autoplay && !state.steps.every((st) => st.done)) {
    $("btn-next").click();
    await new Promise((r) => setTimeout(r, 400));
    while (busy) await new Promise((r) => setTimeout(r, 200));
    await new Promise((r) => setTimeout(r, 3200));
  }
  autoplay = false;
  $("btn-auto").setAttribute("aria-pressed", "false");
  $("btn-auto").textContent = "Play the tour";
});

// Deep links for demos and slides: ?tab=impact opens a tab, ?case=CASE-00023 opens a case.
const LINK = new URLSearchParams(location.search);
loadMerchants().then(refreshAll).then(refreshDatasets).then(async () => {
  if (LINK.get("tab") && TABS[LINK.get("tab")]) await selectTab(LINK.get("tab"));
  if (LINK.get("case")) await openCase(LINK.get("case"));
});
