"use strict";

// Merchant chat. The server answers from the merchant's own records: Sarvam when
// a key is configured, else the on-device model, else the checked template.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PATTERNS = {
  mdr_above_mcc_rate: "Wrong category rate", mdr_above_agreement: "Card rate above agreement",
  mdr_on_protected_instrument: "Charge on free payment", unverified_interchange_passthrough: "Wallet charge",
  gst_on_exempt_settlement: "GST on exempt payment", gst_above_standard_base: "GST on wrong base",
  tax_on_non_eco_flow: "TCS/TDS deducted", refund_debited_twice: "Refund debited twice",
  refund_without_refund_event: "Refund with no refund", payment_missing_from_settlement: "Missing payment",
  mdr_above_turnover_cap: "Above RBI limit", rental_after_return: "Rental after return",
  rental_during_waiver: "Rental in free period",
};
const SPEECH_LANG = { hinglish: "hi-IN", "en-IN": "en-IN" };
const ENGINE_LABEL = (s) => s.startsWith("sarvam") ? "Sarvam AI" : s.startsWith("local:") ? `On-device AI · ${s.slice(6)}` : "Checked answers";

const rupee = (paise) => "₹" + (paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const history = [];
let busy = false;
let sarvamVoice = false;

function sourceLabel(r) {
  if (r.source === "safety") return "Safety notice";
  if (r.source === "paytm") return "Paytm";
  if (r.source === "team") return "Paytm team (a person)";
  if (r.source === "template") return "Checked answer";
  return ENGINE_LABEL(r.source);
}

function scrollDown() { $("thread").scrollTop = $("thread").scrollHeight; }

function addMine(text) {
  const el = document.createElement("div");
  el.className = "msg me";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  $("thread").appendChild(el);
  scrollDown();
}

function addBot(text, r = null) {
  const el = document.createElement("div");
  el.className = "msg bot";
  const tags = (r?.cases || []).slice(0, 4).map((c) =>
    `<span class="case-tag" role="button" tabindex="0" data-case="${esc(c.case_id)}" title="Show the proof for ${esc(c.case_id)}">${esc(PATTERNS[c.pattern] || c.pattern)} · ${esc(c.month)}</span>`).join("");
  const meta = r ? `<div class="meta">
      <span>${esc(sourceLabel(r))}</span>
      ${!["safety", "team"].includes(r.source) ? `<span class="checked">Figures checked</span>` : ""}
      <button class="speak" type="button" aria-label="Listen"><svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z" fill="currentColor"/><path d="M16 8.5a5 5 0 010 7M18.5 6a8.5 8.5 0 010 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>Listen</button>
    </div>` : "";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>${tags ? `<div class="case-tags">${tags}</div>` : ""}${meta}` +
    (r?.note ? `<div class="note">${esc(r.note)}</div>` : "");
  el.querySelector(".speak")?.addEventListener("click", () => speak(text, r.language));
  $("thread").appendChild(el);
  el.querySelectorAll(".case-tag").forEach((tag) => tag.addEventListener("click", () => send(`${tag.dataset.case} ka proof dikhao`)));
  if (r?.ticket) addTicket(r.ticket);
  if (r?.payments?.length) addPayments(r.payments);
  if (r?.proof) addProof(r.proof);
  if (r?.appealable?.length) addAppealCards(r.appealable, r.language);
  if (r?.actions?.length) addDecisionCards(r.actions, r.language);
  scrollDown();
}

// -- the merchant decides review cases right here ------------------------------------------------

function addDecisionCards(cards, language) {
  const wrap = document.createElement("div");
  wrap.className = "decisions";
  wrap.innerHTML = cards.map((c) => `
    <article class="decision" data-case="${esc(c.case_id)}">
      <header><strong>${esc(c.title)}</strong><span>${esc(c.month)}</span></header>
      <div class="decision-amount">${rupee(c.amount_paise)}</div>
      <p>${esc(c.why)}</p>
      <div class="decision-buttons">
        ${c.options.map((o) => `<button type="button" class="${o.action}" data-action="${o.action}">${esc(o.label)}</button>`).join("")}
      </div>
    </article>`).join("");
  wrap.querySelectorAll(".decision").forEach((card) => {
    card.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => confirmDecision(card, btn, language)));
  });
  $("thread").appendChild(wrap);
}

function confirmDecision(card, btn, language) {
  const buttons = card.querySelector(".decision-buttons");
  const original = buttons.innerHTML;
  const filing = btn.dataset.action === "file";
  buttons.innerHTML = `<span class="confirm-q">${filing ? "Pakka? Correction aapke naam se file hogi." : "Pakka? Yeh case band ho jayega, kuch file nahi hoga."}</span>
    <button type="button" class="${btn.dataset.action}" data-yes>${filing ? "Haan, file karo" : "Haan, band karo"}</button>
    <button type="button" class="cancel" data-no>Rehne do</button>`;
  buttons.querySelector("[data-no]").addEventListener("click", () => {
    buttons.innerHTML = original;
    buttons.querySelectorAll("[data-action]").forEach((b) => b.addEventListener("click", () => confirmDecision(card, b, language)));
  });
  buttons.querySelector("[data-yes]").addEventListener("click", async () => {
    buttons.innerHTML = `<span class="confirm-q">Record kar rahe hain…</span>`;
    try {
      const res = await fetch("/api/chat/decide", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_id: card.dataset.case, action: btn.dataset.action, language }),
      });
      const r = await res.json();
      if (!res.ok) throw new Error(r.detail || "Could not record the decision");
      card.classList.add("decided");
      buttons.innerHTML = `<span class="done-tag">${filing ? "✓ Correction filed" : "✓ Marked correct"}</span>`;
      addMine(filing ? "Haan, correction file karo" : "Nahi, yeh charge sahi hai");
      addBot(r.reply, { ...r, actions: [] });
      history.push({ role: "assistant", content: r.reply });
    } catch (err) {
      buttons.innerHTML = original;
      buttons.querySelectorAll("[data-action]").forEach((b) => b.addEventListener("click", () => confirmDecision(card, b, language)));
      showBanner(err.message);
    }
  });
}

function addTicket(t) {
  const el = document.createElement("div");
  el.className = "ticket-card";
  const handoff = t.topic === "handoff";
  el.innerHTML = `<strong>Ticket ${esc(t.ticket_id)}</strong> · ${handoff ? "a person from Paytm's team will reply" : "sent to Paytm's settlement team"}
    <div>${esc(handoff ? "Your conversation so far has been shared with them." : t.records_showed)}</div>
    <div class="ticket-status">Status: ${t.status === "OPEN" ? "open. The reply will come here." : "resolved."}</div>`;
  $("thread").appendChild(el);
}

const PAY_STATUS = { SETTLED: ["Settled", "ok"], LATE: ["Settled late", "warn"], NOT_DUE: ["Not due yet", "info"],
  MISSING: ["Missing: being corrected", "bad"], FAILED: ["Payment failed", "muted"] };

function addPayments(list) {
  const lang = $("language").value === "en-IN" ? "en" : "hi";
  const wrap = document.createElement("div");
  wrap.className = "decisions";
  wrap.innerHTML = list.map((p) => {
    const [label, cls] = PAY_STATUS[p.status] || [p.status, "info"];
    return `<article class="info-card">
      <header><strong>${rupee(p.amount_paise)} · ${esc(p.instrument.replaceAll("_", " "))}</strong><span class="tag ${cls}">${label}</span></header>
      <div class="info-sub">${new Date(p.captured_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} · ${esc(p.txn_id)}</div>
      <p>${esc(lang === "en" ? p.en : p.hi)}</p>
      ${p.utr ? `<dl><dt>Settlement</dt><dd>${esc(p.batch_id)}</dd><dt>Bank UTR</dt><dd>${esc(p.utr)}</dd><dt>Net credited</dt><dd>${rupee(p.net_paise)}</dd></dl>` : ""}
    </article>`;
  }).join("");
  $("thread").appendChild(wrap);
}

function addProof(p) {
  const el = document.createElement("article");
  el.className = "info-card proof-card";
  el.innerHTML = `<header><strong>Proof · ${esc(p.case_id)}</strong><span class="tag ok">${esc(p.verdict)}</span></header>
    <div class="info-sub">${esc(p.title)} · ${esc(p.month)} · ${p.payments} payments</div>
    <div class="decision-amount">${rupee(p.overcharge_paise)} overcharged</div>
    <dl>
      <dt>Rule</dt><dd>${esc(p.rule || "—")}</dd>
      <dt>Source</dt><dd>${esc(p.source || "—")}</dd>
      ${p.example ? `<dt>Example</dt><dd class="mono">${esc(p.example)}</dd>` : ""}
      <dt>Checked</dt><dd>Recomputed under every rounding method, from ${p.records} records</dd>
      ${p.reference ? `<dt>Correction</dt><dd>${esc(p.reference)}</dd>` : ""}
      ${p.credit ? `<dt>Refund</dt><dd>${rupee(p.credit.amount_paise)} on ${esc(p.credit.settlement_date)} · UTR ${esc(p.credit.utr)}</dd>` : `<dt>Status</dt><dd>${esc(p.state.replaceAll("_", " ").toLowerCase())}</dd>`}
    </dl>`;
  $("thread").appendChild(el);
}

function addAppealCards(list, language) {
  const wrap = document.createElement("div");
  wrap.className = "decisions";
  wrap.innerHTML = list.map((c) => `<article class="decision" data-case="${esc(c.case_id)}">
      <header><strong>${esc(c.title)}</strong><span>${esc(c.month)}</span></header>
      <div class="decision-amount">${rupee(c.amount_paise)}</div>
      <p>${esc(c.why_closed)}</p>
      <div class="decision-buttons"><button type="button" class="file" data-appeal>Appeal karo</button></div>
    </article>`).join("");
  wrap.querySelectorAll("[data-appeal]").forEach((btn) => btn.addEventListener("click", () => {
    const card = btn.closest(".decision");
    const box = card.querySelector(".decision-buttons");
    box.innerHTML = `<input class="appeal-reason" maxlength="400" placeholder="Aap sehmat kyun nahi? (optional)">
      <button type="button" class="file" data-send>Appeal bhejo</button>`;
    box.querySelector("input").focus();
    box.querySelector("[data-send]").addEventListener("click", async () => {
      const reason = box.querySelector("input").value;
      box.innerHTML = `<span class="confirm-q">Bhej rahe hain…</span>`;
      try {
        const res = await fetch("/api/chat/appeal", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ case_id: card.dataset.case, reason, language }),
        });
        const r = await res.json();
        if (!res.ok) throw new Error(r.detail || "Could not send the appeal");
        card.classList.add("decided");
        box.innerHTML = `<span class="done-tag">✓ Appeal sent</span>`;
        addMine(reason ? `Appeal: ${reason}` : "Appeal karo");
        addBot(r.reply, r);
      } catch (err) {
        box.innerHTML = `<span class="confirm-q">${esc(err.message)}</span>`;
      }
    });
  }));
  $("thread").appendChild(wrap);
}

// -- inbox: refunds landing, team replies, appeal outcomes arrive on their own ---------------------

let inboxCursor = null;

async function pollInbox() {
  try {
    const url = inboxCursor === null ? "/api/chat/inbox?since=-1" : `/api/chat/inbox?since=${inboxCursor}`;
    const r = await (await fetch(url)).json();
    if (inboxCursor !== null) {
      const lang = $("language").value === "en-IN" ? "en" : "hi";
      for (const m of r.messages) {
        const badge = document.createElement("div");
        badge.className = "found-for-you" + (m.kind === "ticket.reply" ? " team" : "");
        badge.textContent = { "refund.credited": "Paisa aa gaya", "ticket.reply": "Paytm team", "appeal.decided": "Appeal ka faisla" }[m.kind] || "Paytm";
        $("thread").appendChild(badge);
        const text = lang === "en" ? m.text_en : m.text_hi;
        addBot(text, { source: m.kind === "ticket.reply" ? "team" : "paytm", language: lang === "en" ? "en-IN" : "hinglish", cases: [] });
        history.push({ role: "assistant", content: text });
      }
    }
    inboxCursor = r.next;
  } catch (_) { /* the banner covers a lost server */ }
}
setInterval(pollInbox, 4000);

function startDraft() {
  // The model's words as it writes them: visibly unchecked until the guards have read the whole reply.
  const el = document.createElement("div");
  el.className = "msg bot draft";
  el.innerHTML = `<div class="bubble"></div><div class="meta"><span>Writing… not yet checked</span></div>`;
  $("thread").appendChild(el);
  return el;
}

function showTyping() {
  const el = document.createElement("div");
  el.className = "msg bot typing";
  el.innerHTML = `<div class="bubble"><span class="dots"><i></i><i></i><i></i></span><span class="label">Checking your records…</span></div>`;
  $("thread").appendChild(el);
  scrollDown();
  const slow = setTimeout(() => { el.querySelector(".label").textContent = "Translating your answer…"; }, 4000);
  return () => { clearTimeout(slow); el.remove(); };
}

function setChips(list) {
  $("chips").innerHTML = (list || []).map((q) => `<button type="button">${esc(q)}</button>`).join("");
  $("chips").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
}

async function speak(text, language) {
  try {
    const res = await fetch("/api/chat/speak", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }),
    });
    if (res.status === 200) {
      new Audio(URL.createObjectURL(await res.blob())).play();
      return;
    }
  } catch (_) { /* fall through to the browser voice */ }
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text.replace(/₹/g, "rupees "));
  u.lang = SPEECH_LANG[language] || language || "hi-IN";
  const voice = speechSynthesis.getVoices().find((v) => v.lang === u.lang);
  if (voice) u.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true;
  $("send").disabled = true;
  $("input").value = "";
  addMine(text);
  const done = showTyping();
  let draft = null;
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, language: $("language").value, history: history.slice(-6) }),
    });
    if (!res.ok || !res.body) throw new Error(`server said ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", r = null;
    while (r === null) {
      const { value, done: finished } = await reader.read();
      if (finished) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const event = JSON.parse(buffer.slice(0, nl));
        buffer = buffer.slice(nl + 1);
        if (event.type === "token") {
          if (!draft) { done(); draft = startDraft(); }
          draft.querySelector(".bubble").textContent += event.text;
          scrollDown();
        } else if (event.type === "final") {
          r = event.result;
        } else if (event.type === "error") {
          throw new Error(event.detail);
        }
      }
    }
    if (!r) throw new Error("the reply stream ended early");
    if (draft) draft.remove(); else done();
    addBot(r.reply, r);
    setChips(r.suggestions);
    history.push({ role: "user", content: text }, { role: "assistant", content: r.reply });
    $("banner").hidden = true;
  } catch (err) {
    if (draft) draft.remove(); else done();
    $("banner").textContent = "Can't reach the Settlement Teammate server. Is python serve.py running?";
    $("banner").hidden = false;
  } finally {
    busy = false;
    $("send").disabled = false;
    $("input").focus();
  }
}

// -- voice input: Sarvam speech-to-text when configured, else the browser's recogniser ------------

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recorder = null;
let recognizer = null;

function micState(state) {
  const mic = $("mic");
  mic.classList.toggle("listening", state === "listening");
  $("input").placeholder = state === "listening" ? "Sun raha hoon… bolkar mic dabaiye" : "Likhiye ya bol kar poochiye… (any language)";
}

async function startSarvamRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    micState("idle");
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null;
    $("input").placeholder = "Aapki awaaz samajh raha hoon…";
    try {
      const res = await fetch("/api/chat/listen", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
      if (res.status === 200) {
        const heard = await res.json();
        if (heard.text) { send(heard.text); return; }
      }
      showBanner("Awaaz samajh nahi aayi. Dobara boliye ya likhiye.");
    } catch (_) {
      showBanner("Can't reach the Settlement Teammate server. Is python serve.py running?");
    } finally {
      micState("idle");
    }
  };
  recorder.start();
  micState("listening");
}

function startBrowserRecognition() {
  recognizer = new Recognition();
  recognizer.lang = { hinglish: "hi-IN", auto: "hi-IN" }[$("language").value] || $("language").value;
  recognizer.interimResults = true;
  recognizer.onresult = (e) => {
    const text = [...e.results].map((r) => r[0].transcript).join(" ");
    $("input").value = text;
    if (e.results[e.results.length - 1].isFinal) { recognizer.stop(); send(text); }
  };
  recognizer.onerror = () => showBanner("Voice input could not start here. Please type your question.");
  recognizer.onend = () => { micState("idle"); recognizer = null; };
  recognizer.start();
  micState("listening");
}

$("mic").addEventListener("click", async () => {
  if (busy) return;
  if (recorder) { recorder.stop(); return; }
  if (recognizer) { recognizer.stop(); return; }
  try {
    if (sarvamVoice && window.MediaRecorder && navigator.mediaDevices) await startSarvamRecording();
    else if (Recognition) startBrowserRecognition();
    else showBanner("Voice input needs a Sarvam key, or a browser with speech recognition. Please type instead.");
  } catch (_) {
    micState("idle");
    showBanner("Microphone permission was not given. Please type your question.");
  }
});

function showBanner(text) {
  $("banner").textContent = text;
  $("banner").hidden = false;
  setTimeout(() => { $("banner").hidden = true; }, 6000);
}

// -- Paytm speaks first --------------------------------------------------------------------------

async function paytmSpeaksFirst() {
  const done = showTyping();
  try {
    const res = await fetch("/api/chat/proactive", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: $("language").value === "auto" ? "hinglish" : $("language").value }),
    });
    if (!res.ok) throw new Error(String(res.status));
    const r = await res.json();
    done();
    const badge = document.createElement("div");
    badge.className = "found-for-you";
    badge.textContent = "Paytm found this for you";
    $("thread").appendChild(badge);
    addBot(r.reply, r);
    setChips(r.suggestions);
    history.push({ role: "assistant", content: r.reply });
  } catch (_) {
    done();
  }
}

async function boot() {
  try {
    const s = await (await fetch("/api/chat/status")).json();
    sarvamVoice = s.sarvam_configured;
    await pollInbox();  // start the cursor here; earlier events are covered by Paytm's first message
    if (!sarvamVoice && !Recognition) {
      $("mic").classList.add("unavailable");
      $("mic").title = "Voice input needs a Sarvam key (or a browser with speech recognition)";
    }
    $("engine").textContent = ENGINE_LABEL(s.active) + (s.sarvam_configured ? "" : " · Sarvam key not set");
    $("language").innerHTML = `<option value="auto">Auto</option>` +
      s.languages.map((l) => `<option value="${esc(l.code)}">${esc(l.label)}</option>`).join("");
    const first = s.merchant.split(/\s+/)[0];
    if (s.connected) {
      await paytmSpeaksFirst();
    } else {
      addBot(`Namaste ${first} ji! Hum abhi aapke settlements check kar rahe hain. Command center mein story shuru kijiye, phir yahan poochiye.`);
      setChips(s.suggestions);
    }
  } catch (err) {
    $("engine").textContent = "Offline";
    $("banner").textContent = "Can't reach the Settlement Teammate server. Is python serve.py running?";
    $("banner").hidden = false;
  }
}

$("composer").addEventListener("submit", (ev) => { ev.preventDefault(); send($("input").value); });
boot();
