"""Merchant chat: the merchant asks, the assistant answers from their own records.

Three layers, so a language model can make the answer friendlier but never wrong:

  1. Retrieval (deterministic). The question is routed to topics (charges,
     rental, late money, refunds, ...) and the matching facts are read from
     the runtime: proven cases, recoveries, device records, SLA breaches,
     root causes. Every figure here is computed, never generated.
  2. A draft answer (deterministic) is written from those facts. It is always
     correct and is what the merchant sees when no model is available.
  3. Models, each where it is strong:
       Sarvam (SARVAM_API_KEY set) writes a conversational answer from the
       facts in the merchant's language, and translates and speaks.
       The local model (Ollama, gemma3:4b) is the offline backup: it
       understands messages the keyword router cannot place (Devanagari,
       Tamil, unusual phrasing) and translates the checked answer into
       languages other than Hinglish and English.
     Guards then check every figure: a conversational reply may only use
     amounts from this merchant's records; a translation must carry exactly
     the checked answer's figures. Loops, wrong-language replies and overlong
     replies are rejected too. Any failure sends the checked draft instead.

The model never sees another merchant's data, never decides what is owed, and
is told never to ask for OTPs, PINs or bank details.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from mfp.assistant.backends import NETWORK_ERRORS, BackendChain, BackendError
from mfp.core.enums import CaseState
from mfp.runtime.system import Runtime
from mfp.runtime.views import late_settlements

# -- language --------------------------------------------------------------------------

LANGUAGES = {
    "hinglish": "Hinglish (Hindi in Roman script, mixed with simple English, as Indian shopkeepers text)",
    "en-IN": "simple Indian English",
    "hi-IN": "Hindi in Devanagari script",
    "mr-IN": "Marathi", "gu-IN": "Gujarati", "ta-IN": "Tamil", "te-IN": "Telugu",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "bn-IN": "Bengali", "pa-IN": "Punjabi", "od-IN": "Odia",
}
LANGUAGE_LABELS = {
    "hinglish": "Hinglish", "en-IN": "English", "hi-IN": "हिंदी", "mr-IN": "मराठी", "gu-IN": "ગુજરાતી",
    "ta-IN": "தமிழ்", "te-IN": "తెలుగు", "kn-IN": "ಕನ್ನಡ", "ml-IN": "മലയാളം", "bn-IN": "বাংলা",
    "pa-IN": "ਪੰਜਾਬੀ", "od-IN": "ଓଡ଼ିଆ",
}


def inr(paise: int, *, whole: bool = False) -> str:
    """₹1,23,456.78 with Indian digit grouping."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, rem = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    if whole:
        return f"{sign}₹{s}"
    return f"{sign}₹{s}.{rem:02d}"


# -- plain-language descriptions of each pattern ---------------------------------------------

WHY = {
    "mdr_on_protected_instrument": (
        "a payment charge (MDR) was taken on payments that must be free: UPI until 15 Oct 2026, and RuPay debit cards",
        "UPI (15 Oct 2026 tak) aur RuPay debit card payments free hone chahiye the, phir bhi un par charge (MDR) kata"),
    "mdr_above_agreement": (
        "the card charge was higher than the rate in your signed agreement",
        "card par charge aapke signed agreement ke rate se zyada kata"),
    "mdr_above_mcc_rate": (
        "RuPay credit card on UPI payments were charged at the rate for the wrong business category",
        "RuPay credit card on UPI payments par galat business category ka rate laga"),
    "mdr_above_turnover_cap": (
        "debit card charges were above the RBI limit for a business of your size",
        "debit card par charge aapke business size ki RBI limit se zyada kata"),
    "unverified_interchange_passthrough": (
        "a wallet charge was passed on to you, and no published rule says whether you owe it, so it needs your review",
        "wallet payments ka ek charge aap par dala gaya; koi published rule nahi batata ki yeh banta hai ya nahi, isliye yeh aapke review ke liye hai"),
    "gst_on_exempt_settlement": (
        "GST was charged on card payments of ₹2,000 or less, which are exempt",
        "₹2,000 tak ke card payments par GST nahi lagta, phir bhi kata"),
    "gst_above_standard_base": (
        "GST was calculated on the wrong amount",
        "GST galat amount par calculate hua"),
    "tax_on_non_eco_flow": (
        "TCS/TDS was deducted although you do not sell through an e-commerce operator",
        "aap e-commerce operator ke through nahi bechte, phir bhi TCS/TDS kata"),
    "refund_debited_twice": (
        "the same refund was taken from your settlement twice",
        "ek hi refund aapke settlement se do baar kata"),
    "refund_without_refund_event": (
        "money was taken as a refund with no matching refund",
        "refund ke naam par paisa kata, lekin koi refund hua hi nahi"),
    "payment_missing_from_settlement": (
        "customer payments were never included in any settlement",
        "customer ke payments kisi bhi settlement mein nahi aaye"),
    "rental_after_return": (
        "soundbox rental was charged after you returned the device",
        "soundbox wapas karne ke baad bhi rental kata"),
    "rental_during_waiver": (
        "soundbox rental was charged during the free period",
        "free period mein bhi soundbox rental kata"),
}

TOPIC_PATTERNS = {
    "charges": {"mdr_on_protected_instrument", "mdr_above_agreement", "mdr_above_mcc_rate",
                "mdr_above_turnover_cap", "unverified_interchange_passthrough"},
    "tax": {"gst_on_exempt_settlement", "gst_above_standard_base", "tax_on_non_eco_flow"},
    "refund": {"refund_debited_twice", "refund_without_refund_event"},
    "missing": {"payment_missing_from_settlement"},
    "rental": {"rental_after_return", "rental_during_waiver"},
}

KEYWORDS = [  # order is priority
    ("rental", ("rental", "rent", "soundbox", "sound box", "device", "machine", "kiraya", "199", "edc", "pos")),
    ("late", ("late", "delay", "der ", "der?", "der se", "dheere", "kab aay", "kab milega", "pending", "nahi aaya",
              "not received", "t+1", "slow")),
    ("refund", ("refund", "double", "twice", "do baar", "2 baar")),
    ("missing", ("missing", "gayab", "nahi mila", "not settled", "dropped", "chhoot")),
    ("tax", ("gst", "tds", "tcs", "tax")),
    ("charges", ("mdr", "charge", "fee", "commission", "kata", "kaat", "katoti", "deduct", "cut", "rate", "upi",
                 "card", "wallet")),
    ("review", ("review", "verify", "approve", "mujhe kya", "kya karna", "kuch karna", "karna hai", "karna padega",
                "what should i", "do i need", "action", "sign")),
    ("prevent", ("future", "aage", "dobara", "again", "stop", "band", "fix", "root", "kyun ho raha")),
    ("recovered", ("recover", "wapas", "back", "return", "credited", "mila", "refunded")),
    ("greet", ("hi", "hello", "namaste", "namaskar", "hey", "good morning")),
]
TOPIC_HELP = {
    "summary": "overall: how much money was found or returned",
    "rental": "soundbox / device / machine rental charged, including after returning it",
    "late": "settlement arrived late, or when money will come",
    "refund": "refunds debited wrongly or twice",
    "missing": "a customer paid but the money never reached the merchant's settlement",
    "tax": "GST, TCS or TDS deductions",
    "charges": "payment charges / MDR / fees deducted on UPI, cards or wallet, or money cut without reason",
    "review": "what the merchant needs to do, approve or verify",
    "prevent": "whether the problem will happen again in future",
    "greet": "greeting or thanks only",
    "other": "anything else",
}
HINGLISH_WORDS = {"hai", "hain", "kya", "kyun", "kyon", "mera", "mere", "meri", "paisa", "paise", "nahi", "nahin",
                  "kab", "aap", "kitna", "hua", "gaya", "gaye", "kar", "karo", "kata", "kaat", "bhai", "ji", "aaya",
                  "mila", "wapas", "kaise", "yeh", "ye", "tha", "thi", "ho", "hoga", "diya", "liye", "se", "ka", "ki"}


def detect_language(text: str) -> str:
    """The merchant's language from the script they typed in; Roman script is Hinglish if it reads like it."""
    for code, (lo, hi) in SCRIPTS.items():
        if code == "mr-IN":
            continue  # Devanagari is read as Hindi; Marathi can be chosen explicitly
        if any(lo <= ord(ch) <= hi for ch in text):
            return code
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if len(words & HINGLISH_WORDS) >= 1 else "en-IN"


SENSITIVE = ("otp", "pin", "password", "cvv", "upi pin", "bank account number", "card number")

SUGGESTIONS = {
    "summary": ["Kitna paisa wapas aaya?", "Soundbox rental kyun kata?", "Settlement late kyun aaya?"],
    "charges": ["Aage se yeh charge band hoga?", "Kya mujhe kuch karna hai?"],
    "rental": ["Kitna rental wapas milega?", "Total kitna paisa mila?"],
    "late": ["Kya late payment ka paisa milega?", "Total kitna recover hua?"],
    "review": ["Wallet charge kya hai?", "Total kitna recover hua?"],
}


def _topics(question: str) -> list[str]:
    q = f" {question.lower()} "
    found = []
    for topic, words in KEYWORDS:
        # A keyword must start a word ("rent" matches "rental", not "current"); short ones must be whole words.
        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}" + ("" if len(w) > 3 else r"(?![a-z0-9])"), q)
               for w in words):
            found.append(topic)
    specific = [t for t in found if t not in ("greet", "recovered")]
    if len(specific) > 1 and "charges" in specific:
        specific.remove("charges")  # "kata"/"charge" is generic when a specific subject is named
    if specific:
        return specific[:2]
    return found[:1] or ["summary"]


# -- the money guard ----------------------------------------------------------------------

_MONEY = re.compile(
    r"(?:₹|\brs\.?|\binr|रु\.?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    r"|([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:₹|rupees|rupaye|rupaiye|rupay|rupee|rs\b|रुपये|रुपए|रुपया)", re.IGNORECASE)
REGULATORY_FIGURES = {Decimal(v) for v in ("2000", "199", "200", "300", "1000", "100000", "0")}


def _ascii_digits(text: str) -> str:
    """Devanagari, Tamil, Bengali... digits to 0-9, so the guard reads every script."""
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" and not ch.isascii() else ch
                   for ch in text)


def amounts_in(text: str) -> set[Decimal]:
    out = set()
    for m in _MONEY.finditer(_ascii_digits(text)):
        raw = (m.group(1) or m.group(2)).replace(",", "").rstrip(".")
        try:
            out.add(Decimal(raw))
        except ArithmeticError:
            continue
    return out


def allowed_amounts(paise_values: set[int]) -> set[Decimal]:
    allowed = set(REGULATORY_FIGURES)
    for p in paise_values:
        exact = Decimal(abs(p)) / 100
        allowed |= {exact, exact.quantize(Decimal(1), ROUND_HALF_UP), exact.quantize(Decimal(1), ROUND_FLOOR)}
    return allowed


def numbers_in(text: str) -> set[Decimal]:
    """Every figure in the text, with or without a currency sign (dates and counts included)."""
    out = set()
    for raw in re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", _ascii_digits(text)):
        try:
            out.add(Decimal(raw.replace(",", "").rstrip(".")).normalize())
        except ArithmeticError:
            continue
    return out


def unsupported_amounts(reply: str, paise_values: set[int]) -> set[Decimal]:
    allowed = allowed_amounts(paise_values)
    return {a for a in amounts_in(reply) if a.normalize() not in {x.normalize() for x in allowed}}


# -- reply quality ----------------------------------------------------------------------------

SCRIPTS = {"hi-IN": (0x0900, 0x097F), "mr-IN": (0x0900, 0x097F), "bn-IN": (0x0980, 0x09FF),
           "pa-IN": (0x0A00, 0x0A7F), "gu-IN": (0x0A80, 0x0AFF), "od-IN": (0x0B00, 0x0B7F),
           "ta-IN": (0x0B80, 0x0BFF), "te-IN": (0x0C00, 0x0C7F), "kn-IN": (0x0C80, 0x0CFF),
           "ml-IN": (0x0D00, 0x0D7F)}


def strip_preamble(text: str) -> str:
    """Drop a model's lead-in such as "Sure, here is the translation:" and wrapping quotes."""
    text = (text or "").strip().strip('"').strip()
    lines = text.split("\n")
    if len(lines) > 1 and lines[0].rstrip().endswith((":", "：")) and len(lines[0]) < 120:
        text = "\n".join(lines[1:]).strip()
    return text


def reply_problem(reply: str, draft: str, language: str) -> str | None:
    """Why a model reply must not be shown, or None. Small models loop, drift and switch language."""
    if not reply:
        return "empty"
    if len(reply) > max(700, 3 * len(draft)):
        return "too long"
    sentences = [re.sub(r"\W+", " ", x).strip().lower() for x in re.split(r"[.!?।\n]+", reply)]
    sentences = [x for x in sentences if len(x) > 12]
    if len(sentences) != len(set(sentences)):
        return "repeats itself"
    words = reply.lower().split()
    shingles = [" ".join(words[i:i + 5]) for i in range(max(0, len(words) - 4))]
    if shingles and max(shingles.count(x) for x in set(shingles)) >= 3:
        return "repeats itself"
    letters = [ch for ch in reply if ch.isalpha()]
    if letters:
        if language in SCRIPTS:
            lo, hi = SCRIPTS[language]
            if sum(lo <= ord(ch) <= hi for ch in letters) / len(letters) < 0.4:
                return "wrong language"
        elif sum(ch.isascii() for ch in letters) / len(letters) < 0.8:
            return "wrong language"
    return None


# -- facts ------------------------------------------------------------------------------------


@dataclass
class Facts:
    lines: list[str] = field(default_factory=list)          # English, for the model
    draft_en: list[str] = field(default_factory=list)
    draft_hi: list[str] = field(default_factory=list)       # Hinglish
    paise: set[int] = field(default_factory=set)
    cases: list[dict[str, Any]] = field(default_factory=list)

    def money(self, paise: int, *, whole: bool = False) -> str:
        self.paise.add(paise)
        return inr(paise, whole=whole)


class MerchantAssistant:
    def __init__(self, rt: Runtime, merchant_id: str, backends: BackendChain | None = None,
                 lock: AbstractContextManager | None = None) -> None:
        self.rt = rt
        self.merchant_id = merchant_id
        self.backends = backends or BackendChain()
        # Held only while reading the runtime, never while a model is thinking.
        self.lock = lock or nullcontext()

    # -- public ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        m = self.rt.dataset.merchant(self.merchant_id)
        return {**self.backends.status(), "merchant": m.legal_name, "city": m.city,
                "connected": self.merchant_id in self.rt.connected,
                "languages": [{"code": k, "label": v} for k, v in LANGUAGE_LABELS.items()],
                "suggestions": SUGGESTIONS["summary"]}

    def reply(self, question: str, language: str = "auto",
              history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """Answer one merchant message.

        Sarvam (when configured) writes a conversational answer from the facts in
        the merchant's language. Without it, the local model (a) understands
        messages the keyword router cannot place and (b) translates the checked
        answer into languages other than Hinglish and English. Every model output
        passes the amount and quality guards or is replaced by the checked answer.
        """
        started = time.monotonic()
        question = (question or "").strip()[:500]
        if language not in LANGUAGES:
            language = detect_language(question)
        if any(re.search(rf"(?<![a-z]){word}(?![a-z])", question.lower()) for word in SENSITIVE):
            text = ("Paytm kabhi bhi aapka OTP, PIN, password ya CVV nahi maangta. Kisi ke saath share na karein."
                    if language in ("hinglish", "hi-IN") else
                    "Paytm will never ask for your OTP, PIN, password or CVV. Please do not share them with anyone.")
            return self._out(text, language, "safety", ["safety"], Facts(), started, None, "keywords")
        topics, understood_by = _topics(question), "keywords"
        if topics == ["summary"] and len(question) > 3:
            classified = self._classify(question)
            if classified:
                topics, understood_by = [classified], self._classifier_name()
        with self.lock:
            facts = self._facts(topics, question)
            system = self._system(language)
            known_paise = facts.paise | self._all_paise()
        hinglish_draft = language in ("hinglish", "hi-IN")
        draft = " ".join(facts.draft_hi if hinglish_draft else facts.draft_en)

        source, note, text = "template", None, draft
        sarvam, local = self.backends.sarvam, self.backends.local
        if sarvam.client.configured:
            try:
                candidate = sarvam.complete(system, self._messages(question, facts, draft, history))
                text, source, note = self._vet(candidate, draft, language, known_paise, sarvam.name, strict=False)
            except (*NETWORK_ERRORS, BackendError) as exc:
                note = f"{sarvam.name} unavailable ({type(exc).__name__})"
        if source == "template" and language not in ("hinglish", "en-IN"):
            translated = self._translate(draft, language)  # Sarvam's translation model, when configured
            if translated:
                text, source, note2 = self._vet(translated, draft, language, known_paise, "sarvam:mayura", strict=True)
                note = note2 or note
            elif local.available():
                try:
                    candidate = local.complete(self._polish_system(language), self._polish_messages(question, draft))
                    text, source, note2 = self._vet(candidate, draft, language, known_paise, local.name, strict=True)
                    note = note2 or note
                except (*NETWORK_ERRORS, BackendError) as exc:
                    note = f"{local.name} unavailable ({type(exc).__name__})"
            if source == "template":
                text = draft
        return self._out(text, language, source, topics, facts, started, note, understood_by)

    def _vet(self, candidate: str, draft: str, language: str, known_paise: set[int], name: str,
             *, strict: bool) -> tuple[str, str, str | None]:
        """(text, source, note): the model's text if it passes every guard, else the checked draft."""
        candidate = strip_preamble(candidate)
        problem = reply_problem(candidate, draft, language)
        if problem:
            return draft, "template", f"{name} reply rejected ({problem}); the checked answer was used"
        if strict:  # a translation must carry exactly the checked answer's figures
            if amounts_in(candidate) != amounts_in(draft) or not numbers_in(candidate) <= numbers_in(draft):
                return draft, "template", f"{name} changed a figure; the checked answer was used"
        else:
            bad = unsupported_amounts(candidate, known_paise)
            if bad:
                return draft, "template", (f"{name} mentioned {', '.join('₹' + str(x) for x in sorted(bad))}, "
                                           "which is not in the records; the checked answer was used")
        return candidate, name, None

    def _classifier_name(self) -> str:
        return self.backends.sarvam.name if self.backends.sarvam.client.configured else self.backends.local.name

    def _classify(self, question: str) -> str | None:
        """Ask a model which topic an unplaced message is about. The answer only picks which facts to read."""
        system = ("Classify a small Indian merchant's chat message about their Paytm settlements into one topic:\n"
                  + "\n".join(f"- {k}: {v}" for k, v in TOPIC_HELP.items())
                  + '\nReply with JSON only: {"topic": "<one topic>"}')
        messages = [{"role": "user", "content": question}]
        schema = {"type": "object", "properties": {"topic": {"type": "string", "enum": list(TOPIC_HELP)}},
                  "required": ["topic"]}
        backends = []
        if self.backends.sarvam.client.configured:
            backends.append(lambda: self.backends.sarvam.complete(system, messages))
        if self.backends.local.available():
            backends.append(lambda: self.backends.local.complete(system, messages, schema=schema, max_tokens=30))
        for call in backends:
            try:
                raw = call()
                match = re.search(r"\{.*\}", raw, re.S)
                topic = json.loads(match.group(0)).get("topic") if match else None
            except (*NETWORK_ERRORS, BackendError, AttributeError):
                continue
            if topic in TOPIC_HELP:
                return None if topic in ("summary", "other") else topic
        return None

    def speak(self, text: str, language: str) -> bytes | None:
        """Sarvam text-to-speech when configured; the browser speaks otherwise."""
        client = self.backends.sarvam.client
        if not client.configured:
            return None
        code = "hi-IN" if language == "hinglish" else language
        try:
            return client.speak(text, code)
        except (*NETWORK_ERRORS, BackendError):
            return None

    # -- internals ---------------------------------------------------------------------

    def _translate(self, text: str, language: str) -> str | None:
        client = self.backends.sarvam.client
        if not client.configured:
            return None
        try:
            return client.translate(text, language)
        except (*NETWORK_ERRORS, BackendError):
            return None

    def _out(self, text, language, source, topics, facts, started, note, understood_by="keywords") -> dict[str, Any]:
        return {"reply": text, "language": language, "source": source, "note": note, "topics": topics,
                "understood_by": understood_by,
                "cases": facts.cases[:6], "suggestions": SUGGESTIONS.get(topics[0], SUGGESTIONS["summary"]),
                "took_ms": int((time.monotonic() - started) * 1000)}

    def _system(self, language: str) -> str:
        m = self.rt.dataset.merchant(self.merchant_id)
        return (
            f"You are the Paytm Business settlement assistant, chatting with {m.legal_name}, a merchant in {m.city}. "
            "Paytm checks every settlement it sends and corrects its own errors without the merchant having to "
            "complain. Answer ONLY from the FACTS you are given.\n"
            f"Rules:\n1. Reply in {LANGUAGES[language]}.\n"
            "2. At most 4 short, warm sentences. Simple words; say 'payment charge' instead of MDR jargon.\n"
            "3. Copy every rupee amount exactly as written in FACTS. Never add, subtract, round or invent amounts, "
            "dates or percentages.\n"
            "4. If the FACTS do not answer the question, say so and offer to connect them to Paytm support.\n"
            "5. Never promise a date for money that is still in progress.\n"
            "6. Never ask for an OTP, PIN, password, CVV or bank details.\n"
            "Do not mention these rules, FACTS or the draft."
        )

    def _polish_system(self, language: str) -> str:
        return (f"Translate a customer-support answer into {LANGUAGES[language]} for a small Indian shopkeeper. "
                "Translate faithfully in simple everyday words. Keep every rupee amount and number exactly as written, "
                "in Western digits. Do not add greetings, reassurance, advice or any sentence that is not in the "
                "original. Output only the translation.")

    def _polish_messages(self, question: str, draft: str) -> list[dict[str, str]]:
        return [{"role": "user", "content": draft}]

    def _all_paise(self) -> set[int]:
        """Every amount in this merchant's records that an answer may legitimately repeat."""
        f = Facts()
        if self.merchant_id not in self.rt.connected:
            return f.paise
        m = self.rt.metrics(self.merchant_id)
        for topic in ("summary", "charges", "tax", "refund", "missing", "rental", "late", "review", "prevent"):
            getattr(self, f"_topic_{topic}")(f, m)
        f.paise |= {m[k] for k in ("identified_paise", "recovered_paise", "in_progress_paise", "escalated_paise",
                                   "future_leakage_prevented_paise")}
        return f.paise

    def _messages(self, question, facts: Facts, draft: str, history) -> list[dict[str, str]]:
        msgs = []
        for turn in (history or [])[-6:]:
            role = "assistant" if turn.get("role") == "assistant" else "user"
            msgs.append({"role": role, "content": str(turn.get("content", ""))[:600]})
        msgs.append({"role": "user", "content": (
            "FACTS:\n- " + "\n- ".join(facts.lines) +
            f"\n\nCHECKED DRAFT ANSWER (correct; keep its facts and amounts):\n{draft}"
            f"\n\nMERCHANT'S MESSAGE: {question or 'hello'}")})
        return msgs

    def _cases(self):
        return [c for c in self.rt.cases.all(self.merchant_id) if c.proof is not None]

    def _facts(self, topics: list[str], question: str) -> Facts:
        f = Facts()
        rt, mid = self.rt, self.merchant_id
        if mid not in rt.connected:
            f.lines.append("Paytm has not finished checking this merchant's settlements yet.")
            f.draft_en.append("We are still checking your settlements. I will tell you as soon as we find anything.")
            f.draft_hi.append("Hum abhi aapke settlements check kar rahe hain. Kuch milte hi aapko bataayenge.")
            return f
        m = rt.metrics(mid)
        f.lines.append(
            f"As of {rt.clock.today():%d %b %Y}: Paytm found {f.money(m['identified_paise'])} deducted in error "
            f"across {m['proven_cases']} proven cases; {f.money(m['recovered_paise'])} is already back in the "
            f"merchant's account; {f.money(m['in_progress_paise'])} is still being corrected; "
            f"{m['escalated']} case(s) worth {f.money(m['escalated_paise'])} wait for the merchant's review; "
            f"future wrong charges of {f.money(m['future_leakage_prevented_paise'])} were stopped by fixing the cause.")

        case_match = re.search(r"case[-\s]?0*(\d+)", question, re.IGNORECASE)
        if case_match:
            self._case_facts(f, f"CASE-{int(case_match.group(1)):05d}")
        for topic in topics:
            getattr(self, f"_topic_{topic}", self._topic_summary)(f, m)
        return f

    def _topic_summary(self, f: Facts, m: dict) -> None:
        if not m["identified_paise"]:
            f.draft_en.append("Good news: every settlement we checked is correct. Nothing was deducted in error.")
            f.draft_hi.append("Achhi khabar: humne jitne settlements check kiye, sab sahi hain. Koi galat katoti nahi mili.")
            return
        f.draft_en.append(
            f"We checked all your settlements and found {inr(m['identified_paise'])} deducted in error. "
            f"{inr(m['recovered_paise'])} is already back in your account.")
        f.draft_hi.append(
            f"Humne aapke saare settlements check kiye aur {inr(m['identified_paise'])} galat kata hua mila. "
            f"Isme se {inr(m['recovered_paise'])} aapke account mein wapas aa chuka hai.")
        if m["in_progress_paise"]:
            f.draft_en.append(f"{inr(m['in_progress_paise'])} is still being corrected.")
            f.draft_hi.append(f"{inr(m['in_progress_paise'])} ki correction abhi chal rahi hai.")
        if m["escalated"]:
            f.draft_en.append(f"{m['escalated']} case(s) need your review.")
            f.draft_hi.append(f"{m['escalated']} case aapke review ke liye rakhe gaye hain.")
        groups = self._groups(set().union(*TOPIC_PATTERNS.values()))
        for pattern, g in sorted(groups.items(), key=lambda kv: -kv[1]["amount"])[:4]:
            f.lines.append(self._group_line(f, pattern, g))

    _topic_greet = _topic_summary
    _topic_recovered = _topic_summary

    def _groups(self, patterns: set[str]) -> dict[str, dict]:
        groups: dict[str, dict] = {}
        for c in self._cases():
            if c.pattern not in patterns:
                continue
            g = groups.setdefault(c.pattern, {"cases": [], "amount": 0, "recovered": 0, "months": set(),
                                              "review": 0, "payments": 0})
            g["cases"].append(c)
            g["amount"] += c.claimed_paise or (c.proof.discrepancy_paise if c.state is CaseState.ESCALATED else 0)
            g["recovered"] += c.recovered_paise
            g["months"].add(c.month)
            g["payments"] += len(c.txn_ids)
            g["review"] += c.state is CaseState.ESCALATED
        return groups

    def _group_line(self, f: Facts, pattern: str, g: dict) -> str:
        months = sorted(g["months"])
        span = months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}"
        for c in g["cases"][:3]:
            f.cases.append({"case_id": c.case_id, "pattern": c.pattern, "month": c.month,
                            "amount_paise": c.claimed_paise or (c.proof.discrepancy_paise if c.proof else 0),
                            "state": str(c.state)})
        line = (f"{WHY[pattern][0].capitalize()} ({span}, {g['payments']} payments): "
                f"{f.money(g['amount'])} in question, {f.money(g['recovered'])} returned so far")
        if g["review"]:
            line += f", {g['review']} case(s) waiting for the merchant's review"
        return line + "."

    def _topic_by_patterns(self, f: Facts, topic: str, none_en: str, none_hi: str) -> None:
        groups = self._groups(TOPIC_PATTERNS[topic])
        if not groups:
            f.lines.append(none_en)
            f.draft_en.append(none_en)
            f.draft_hi.append(none_hi)
            return
        ranked = sorted(groups.items(), key=lambda kv: -kv[1]["amount"])
        for pattern, g in ranked:
            f.lines.append(self._group_line(f, pattern, g))
        for pattern, g in ranked[:2]:  # a chat answer covers the two largest; the rest are summarised
            en, hi = WHY[pattern]
            pending = g["amount"] - g["recovered"]
            if g["review"] == len(g["cases"]):
                f.draft_en.append(f"{en.capitalize()} ({inr(g['amount'])}). Nothing has been filed; it needs your review.")
                f.draft_hi.append(f"{hi[0].upper() + hi[1:]} ({inr(g['amount'])}). Yeh file nahi hua hai, aapke review "
                                  "ke liye rakha gaya hai.")
                continue
            f.draft_en.append(f"{en.capitalize()}: {inr(g['amount'])} in total, and {inr(g['recovered'])} is already "
                              "back in your account.")
            f.draft_hi.append(f"{hi[0].upper() + hi[1:]}: kul {inr(g['amount'])}, jisme se {inr(g['recovered'])} "
                              "aapke account mein wapas aa chuka hai.")
            if pending > 0 and not g["review"]:
                f.paise.add(pending)
                f.draft_en.append(f"The remaining {inr(pending)} is being corrected.")
                f.draft_hi.append(f"Baaki {inr(pending)} ki correction chal rahi hai.")
        if len(ranked) > 2:
            f.draft_en.append(f"We also corrected {len(ranked) - 2} other kind(s) of error of this type.")
            f.draft_hi.append(f"Isi tarah ki {len(ranked) - 2} aur galtiyan bhi theek ki gayi hain.")
        for rc in self.rt.root_causes(self.merchant_id):
            if rc.pattern in TOPIC_PATTERNS[topic] and rc.status == "APPLIED":
                f.lines.append(f"Cause fixed: {rc.cause}. Fix: {rc.prevention_action}.")

    def _topic_charges(self, f: Facts, m: dict) -> None:
        self._topic_by_patterns(f, "charges", "No payment charges were found above the correct rate.",
                                "Koi bhi payment charge sahi rate se zyada nahi mila.")

    def _topic_tax(self, f: Facts, m: dict) -> None:
        self._topic_by_patterns(f, "tax", "No GST, TCS or TDS was deducted in error.",
                                "Koi GST, TCS ya TDS galat nahi kata.")

    def _topic_refund(self, f: Facts, m: dict) -> None:
        self._topic_by_patterns(f, "refund", "No refund was debited twice.", "Koi refund do baar nahi kata.")

    def _topic_missing(self, f: Facts, m: dict) -> None:
        self._topic_by_patterns(f, "missing", "Every customer payment reached a settlement.",
                                "Har customer payment settlement mein aaya.")

    def _topic_rental(self, f: Facts, m: dict) -> None:
        view = self.rt.view(self.merchant_id)
        for d in view.devices:
            state = (f"returned on {d.returned_on:%d %b %Y} (pickup ref {d.return_ref})" if d.returned_on
                     else "still with the merchant")
            f.lines.append(f"Device {d.device_id} ({d.device_type.lower()}): rental {f.money(d.monthly_rental_paise)} "
                           f"a month, free until {d.rental_free_until:%d %b %Y}, {state}.")
        self._topic_by_patterns(f, "rental", "Your device rental has been charged correctly.",
                                "Aapka device rental sahi kata hai.")

    def _topic_late(self, f: Facts, m: dict) -> None:
        late = late_settlements(self.rt, self.merchant_id)
        if not late["payments"]:
            f.lines.append("No settlement arrived late.")
            f.draft_en.append("All your settlements arrived on time.")
            f.draft_hi.append("Aapke saare settlements time par aaye.")
            return
        worst = max(r["worst_days_late"] for r in late["by_month"].values())
        f.lines.append(
            f"{late['payments']} payments worth {f.money(late['held_up_paise'])} reached the merchant late, on average "
            f"{late['average_days_late']} banking days after the agreed date, at worst {worst} days. All of them have "
            "arrived; no money is missing. Delays are reported to Paytm settlement operations to be fixed; the "
            "agreement has no penalty, so nothing extra is paid for a delay.")
        f.draft_en.append(
            f"{late['payments']} of your payments ({inr(late['held_up_paise'])}) reached you late, at worst {worst} "
            "banking days after the agreed date. All of it has arrived and nothing is missing. We have reported the "
            "delays to our settlement team to fix.")
        f.draft_hi.append(
            f"Aapke {late['payments']} payments ({inr(late['held_up_paise'])}) der se aaye, sabse zyada {worst} "
            "banking din late. Saara paisa aa chuka hai, kuch bhi missing nahi hai. Humne yeh delay settlement team ko "
            "report kar diya hai taaki theek ho.")

    def _topic_review(self, f: Facts, m: dict) -> None:
        waiting = self.rt.cases.in_state(CaseState.ESCALATED, merchant_id=self.merchant_id)
        if not waiting:
            f.draft_en.append("Nothing needs your action right now. We handle everything we can prove.")
            f.draft_hi.append("Abhi aapko kuch nahi karna hai. Jo hum prove kar sakte hain, woh hum khud theek karte hain.")
            return
        total = sum(c.proof.discrepancy_paise for c in waiting if c.proof)
        f.lines.append(f"{len(waiting)} case(s) worth {f.money(total)} are waiting for the merchant's review because "
                       "no published rule settles them; Paytm files them only if the merchant or support approves.")
        for c in waiting[:4]:
            f.lines.append(f"{c.case_id}: {WHY[c.pattern][0]} in {c.month}, "
                           f"{f.money(c.proof.discrepancy_paise if c.proof else 0)}.")
            f.cases.append({"case_id": c.case_id, "pattern": c.pattern, "month": c.month,
                            "amount_paise": c.proof.discrepancy_paise if c.proof else 0, "state": str(c.state)})
        f.draft_en.append(f"{len(waiting)} case(s) worth {inr(total)} need your review, because no published rule "
                          "decides them. Open them in the Paytm Business app to approve or dismiss.")
        f.draft_hi.append(f"{len(waiting)} case ({inr(total)}) aapke review ke liye hain, kyunki koi published rule "
                          "inka faisla nahi karta. Paytm Business app mein kholkar approve ya dismiss karein.")

    def _topic_prevent(self, f: Facts, m: dict) -> None:
        fixed = [rc for rc in self.rt.root_causes(self.merchant_id) if rc.status == "APPLIED"]
        for rc in fixed:
            f.lines.append(f"Cause: {rc.cause}. Fix applied: {rc.prevention_action}.")
        f.draft_en.append(f"We fixed {len(fixed)} cause(s) at the source, which stops about "
                          f"{inr(m['future_leakage_prevented_paise'])} of future wrong charges. If one comes back, "
                          "we will catch it again.")
        f.draft_hi.append(f"Humne {len(fixed)} wajah jadd se theek kar di, jisse aage lagbhag "
                          f"{inr(m['future_leakage_prevented_paise'])} ki galat katoti ruk jaayegi. Agar dobara hua, "
                          "hum phir pakad lenge.")

    def _case_facts(self, f: Facts, case_id: str) -> None:
        try:
            c = self.rt.cases.get(case_id)
        except KeyError:
            f.lines.append(f"There is no case {case_id} for this merchant.")
            return
        if c.merchant_id != self.merchant_id:
            f.lines.append(f"There is no case {case_id} for this merchant.")
            return
        claim = self.rt.followup.claims.get(c.claim_id) if c.claim_id else None
        amount = c.claimed_paise or (c.proof.discrepancy_paise if c.proof else 0)
        f.lines.append(f"{c.case_id}: {WHY.get(c.pattern, (c.pattern, c.pattern))[0]} in {c.month}, "
                       f"{len(c.txn_ids)} payments, {f.money(amount)}; status {str(c.state).replace('_', ' ').lower()}; "
                       f"returned {f.money(c.recovered_paise)}"
                       + (f"; correction reference {claim.reference}" if claim and claim.reference else "") + ".")
        f.cases.append({"case_id": c.case_id, "pattern": c.pattern, "month": c.month, "amount_paise": amount,
                        "state": str(c.state)})
        f.draft_en.append(f"{c.case_id}: {inr(amount)} for {c.month}, status "
                          f"{str(c.state).replace('_', ' ').lower()}, {inr(c.recovered_paise)} returned.")
        f.draft_hi.append(f"{c.case_id}: {c.month} ke liye {inr(amount)}, status "
                          f"{str(c.state).replace('_', ' ').lower()}, {inr(c.recovered_paise)} wapas aaya.")
