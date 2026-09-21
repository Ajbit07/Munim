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

const history = [];
let busy = false;
let sarvamVoice = false;

function sourceLabel(r) {
  if (r.source === "safety") return "Safety notice";
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
    `<span class="case-tag" title="${esc(c.case_id)} · ${esc(c.month)}">${esc(PATTERNS[c.pattern] || c.pattern)} · ${esc(c.month)}</span>`).join("");
  const meta = r ? `<div class="meta">
      <span>${esc(sourceLabel(r))}</span>
      ${r.source !== "safety" ? `<span class="checked">Figures checked</span>` : ""}
      <button class="speak" type="button" aria-label="Listen"><svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z" fill="currentColor"/><path d="M16 8.5a5 5 0 010 7M18.5 6a8.5 8.5 0 010 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>Listen</button>
    </div>` : "";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>${tags ? `<div class="case-tags">${tags}</div>` : ""}${meta}` +
    (r?.note ? `<div class="note">${esc(r.note)}</div>` : "");
  el.querySelector(".speak")?.addEventListener("click", () => speak(text, r.language));
  $("thread").appendChild(el);
  scrollDown();
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
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, language: $("language").value, history: history.slice(-6) }),
    });
    if (!res.ok) throw new Error(`server said ${res.status}`);
    const r = await res.json();
    done();
    addBot(r.reply, r);
    setChips(r.suggestions);
    history.push({ role: "user", content: text }, { role: "assistant", content: r.reply });
    $("banner").hidden = true;
  } catch (err) {
    done();
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
