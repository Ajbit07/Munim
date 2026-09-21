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
import os
import re
import time
import unicodedata
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import date
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

SHORT_TITLE = {  # (English, Hinglish) card headings
    "mdr_on_protected_instrument": ("Charge on a free payment", "Free payment par charge"),
    "mdr_above_agreement": ("Card rate above your agreement", "Agreement se zyada card rate"),
    "mdr_above_mcc_rate": ("Wrong business category rate", "Galat category ka rate"),
    "mdr_above_turnover_cap": ("Debit charge above the RBI limit", "RBI limit se zyada debit charge"),
    "unverified_interchange_passthrough": ("Wallet charge passed to you", "Wallet charge aap par dala gaya"),
    "gst_on_exempt_settlement": ("GST on an exempt payment", "Chhoot wale payment par GST"),
    "gst_above_standard_base": ("GST on the wrong amount", "Galat amount par GST"),
    "tax_on_non_eco_flow": ("TCS/TDS deducted", "TCS/TDS kata"),
    "refund_debited_twice": ("Refund taken twice", "Refund do baar kata"),
    "refund_without_refund_event": ("Refund with no refund", "Bina refund ke katoti"),
    "payment_missing_from_settlement": ("Payment missing", "Payment nahi aaya"),
    "rental_after_return": ("Rental after return", "Wapsi ke baad rental"),
    "rental_during_waiver": ("Rental in the free period", "Free period mein rental"),
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
    ("handoff", ("insaan", "human", "kisi se baat", "customer care", "support team", "agent se", "real person",
                 "call karo", "call me", "baat karni", "talk to someone", "talk to a person", "इंसान", "किसी से बात")),
    ("appeal", ("appeal", "sehmat nahi", "disagree", "galat faisla", "dobara check", "reconsider", "not fair",
                "unfair", "reopen", "फिर से देखो")),
    ("proof", ("proof", "saboot", "sabut", "utr", "evidence", "kaise pata", "how do you know", "reference number",
               "prove", "सबूत")),
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
    ("prevent", ("future", "aage", "dobara hoga", "again", "stop", "band hoga", "band ho", "band karo", "fix",
                 "root", "kyun ho raha")),
    ("recovered", ("recover", "wapas", "back", "return", "credited", "mila", "refunded")),
    ("greet", ("hi", "hello", "namaste", "namaskar", "hey", "good morning", "thanks", "thank you", "thank u",
               "shukriya", "dhanyavad", "dhanyawad", "theek hai", "ok", "okay", "accha", "achha")),
]
TOPIC_HELP = {
    "appeal": "disagrees with a decision or a closed or rejected case and wants it looked at again or reopened",
    "handoff": "wants to talk to a human, Paytm staff, an agent or customer care, or wants a call",
    "proof": "wants proof or evidence, a UTR or reference number, or asks how Paytm knows a charge was wrong",
    "payment": "asks where one specific customer payment is, or whether a particular payment has settled",
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
CLASSIFIER_EXAMPLES = (  # a few worked examples; the model generalises from these to any wording
    ("yeh case band kyun kar diya, mujhe paisa chahiye", "appeal"),
    ("aapne galat reject kiya, phir se dekho", "appeal"),
    ("kisi se phone pe baat karni hai", "handoff"),
    ("mujhe kaise pata ki yeh sahi hai?", "proof"),
    ("kal ka 500 wala payment kahan gaya", "payment"),
    ("aage se yeh charge lagega kya?", "prevent"),
)
EXPLICIT = ("handoff", "appeal", "proof", "payment")   # requests the merchant makes, not subjects they ask about
HINGLISH_WORDS = {"hai", "hain", "kya", "kyun", "kyon", "mera", "mere", "meri", "paisa", "paise", "nahi", "nahin",
                  "kab", "aap", "kitna", "hua", "gaya", "gaye", "kar", "karo", "kata", "kaat", "bhai", "ji", "aaya",
                  "mila", "wapas", "kaise", "yeh", "ye", "tha", "thi", "ho", "hoga", "diya", "liye", "se", "ka", "ki",
                  "ke", "rahe", "raha", "rahi", "tak", "apne", "aapke", "aapka", "mein", "isliye", "abhi", "kuch", "bhi",
                  "aur", "humne", "gayi"}


def detect_language(text: str) -> str:
    """The merchant's language from the script they typed in; Roman script is Hinglish if it reads like it."""
    for code, (lo, hi) in SCRIPTS.items():
        if code == "mr-IN":
            continue  # Devanagari is read as Hindi; Marathi can be chosen explicitly
        if any(lo <= ord(ch) <= hi for ch in text):
            return code
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if len(words & HINGLISH_WORDS) >= 1 else "en-IN"


REPORT_WORDS = ("phir bhi", "fir bhi", "still", "galat", "wrong", "kat raha", "kat rahe", "kaat liya", "kaat liye",
                "return kar", "wapas kar", "lauta", "nahi aaya", "nahi mila", "not received", "missing", "extra",
                "zyada", "double", "problem", "complaint", "shikayat", "गलत", "फिर भी", "वापस कर", "नहीं आया")


def is_report(question: str) -> bool:
    """The merchant is telling us about a problem, not only asking."""
    q = question.lower()
    return any(w in q for w in REPORT_WORDS)


def review_reason(case, hinglish: bool) -> str:
    """Why this case needs the merchant, in words a shopkeeper can act on."""
    reason = ((case.escalation or {}).get("reason") or "").lower()
    if "assumed" in reason or "published rule" in reason or "no rule" in reason:
        return ("Koi published rule nahi batata ki yeh charge aap par banta hai. Aapke agreement mein yeh charge nahi hai "
                "to correction file karein; hai to 'Sahi hai' dabaiye." if hinglish else
                "No published rule says whether you owe this charge. If your agreement does not include it, file the "
                "correction; if it does, mark it correct.")
    if "never arrived" in reason:
        return ("Correction approve hua tha par paisa aapke account mein nahi aaya. File karein to hum dobara maangenge."
                if hinglish else "The correction was approved but the money never reached you. File to ask again.")
    if "no response" in reason or "unresponsive" in reason:
        return ("Settlement team ne jawab nahi diya. File karein to hum dobara bhejenge." if hinglish else
                "Settlement ops did not respond. File to send it again.")
    if "rejection" in reason:
        return ("Settlement team ne mana kiya aur hum khud jawab nahi de sakte. Aap chahein to dobara file karein."
                if hinglish else "Settlement ops said no and we cannot answer it ourselves. You can file it again.")
    return ("Hum ise khud prove nahi kar sake, isliye faisla aapka hai." if hinglish else
            "We could not prove this ourselves, so the decision is yours.")


MONTHS = {m: i for i, names in enumerate(
    (("jan", "january", "जनवरी"), ("feb", "february", "फरवरी"), ("mar", "march", "मार्च"), ("apr", "april", "अप्रैल"),
     ("may", "मई"), ("jun", "june", "जून"), ("jul", "july", "जुलाई"), ("aug", "august", "अगस्त"),
     ("sep", "sept", "september", "सितंबर"), ("oct", "october", "अक्टूबर"), ("nov", "november", "नवंबर"),
     ("dec", "december", "दिसंबर")), start=1) for m in names}
PAYMENT_WORDS = ("payment", "transaction", "txn", "paisa", "paise", "kahan", "kab", "where", "status", "aaya",
                 "mila", "settle", "पेमेंट", "कहाँ", "कब")


def parse_payment_query(question: str, year: int) -> tuple[set[int], date | None] | None:
    """Amounts (in paise) and a date the merchant mentions when asking about one payment, or None."""
    q = _ascii_digits(question.lower())
    day = None
    spans = []  # where the date was written, so its digits are not read as an amount
    for m in re.finditer(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z\u0900-\u097f]+)", q):
        if m.group(2) in MONTHS:
            spans.append(m.span())
            if day is None:
                try:
                    day = date(year, MONTHS[m.group(2)], int(m.group(1)))
                except ValueError:
                    day = None
    if day is None:
        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", q)
        if m:
            spans.append(m.span())
            try:
                day = date(year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                day = None
    amounts = {int(a * 100) for a in amounts_in(q)}
    if not amounts:
        stripped = q
        for a, b in sorted(spans, reverse=True):
            stripped = stripped[:a] + " " + stripped[b:]
        amounts = {int(Decimal(x.replace(",", "")) * 100) for x in re.findall(r"\b\d[\d,]*(?:\.\d{1,2})?\b", stripped)
                   if Decimal(x.replace(",", "")) >= 10}
    if not (amounts or day) or not any(w in q for w in PAYMENT_WORDS):
        return None
    return amounts, day


SENSITIVE = ("otp", "pin", "password", "cvv", "upi pin", "bank account number", "card number")

SUGGESTIONS = {
    "summary": ["Kitna paisa wapas aaya?", "Soundbox rental kyun kata?", "Settlement late kyun aaya?"],
    "charges": ["Aage se yeh charge band hoga?", "Kya mujhe kuch karna hai?"],
    "rental": ["Kitna rental wapas milega?", "Total kitna paisa mila?"],
    "late": ["Kya late payment ka paisa milega?", "Total kitna recover hua?"],
    "review": ["Wallet charge kya hai?", "Total kitna recover hua?"],
    "proactive": ["Soundbox rental kyun kata?", "Kya mujhe kuch karna hai?", "Aage se yeh band hoga?"],
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


RETURNED_WORDS = ("wapas", "return", "credited", "credit ho", "back in your", "mil gaya", "mil chuka", "refund ho",
                  "recovered", "correct kiya", "correct ho", "theek kar diya", "reversed", "reverse ho", "refunded",
                  "वापस", "जमा", "परत", "திரும்ப")
PENDING_WORDS = ("review", "approve", "dismiss", "verify", "progress", "chal rahi", "pending", "aayega", "on its way",
                 "will reach", "soon", "jaldi", "file kar", "filed", "समीक्षा", "प्रक्रिया")


OVERCLAIM = re.compile(r"sab (set|theek|thik|sorted|done|clear)|everything (is|has been) (fixed|sorted|settled|done|resolved)"
                       r"|all (fixed|sorted|settled|resolved)|सब (ठीक|सेट)|kuch (bhi )?baaki nahi|nothing (is )?(left|pending)",
                       re.IGNORECASE)


def claims_returned(reply: str, not_returned_paise: set[int]) -> str | None:
    """A sentence that calls money 'returned' while naming an amount still under review or in progress."""
    if not not_returned_paise:
        return None
    risky = {x.normalize() for x in allowed_amounts(not_returned_paise)} - {x.normalize() for x in REGULATORY_FIGURES}
    for sentence in re.split(r"(?<=[.!?।])\s+|\n+", reply):
        low = sentence.lower()
        named = {a.normalize() for a in amounts_in(sentence)} & risky
        if named and any(w in low for w in RETURNED_WORDS) and not any(w in low for w in PENDING_WORDS):
            return "₹" + str(sorted(named)[0])
    return None


def local_writes_enabled() -> bool:
    """The local model writes replies unless MFP_LOCAL_WRITES=0; it only translates then."""
    return os.environ.get("MFP_LOCAL_WRITES", "1") != "0"


def _primary_amount(draft: str) -> Decimal | None:
    """The first rupee figure of the checked answer: the one a reply must not leave out."""
    match = _MONEY.search(_ascii_digits(draft))
    if not match:
        return None
    return Decimal((match.group(1) or match.group(2)).replace(",", "").rstrip(".")).normalize()


def reply_problem(reply: str, draft: str, language: str) -> str | None:
    """Why a model reply must not be shown, or None. Small models loop, drift and switch language."""
    if not reply:
        return "empty"
    if len(reply) > max(900, 3 * len(draft)):
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
        elif language == "en-IN" and len(set(re.findall(r"[a-z]+", reply.lower())) & HINGLISH_WORDS - {"ho"}) >= 3:
            return "wrong language"
    return None


# -- facts ------------------------------------------------------------------------------------


@dataclass
class Facts:
    ticket: dict[str, Any] | None = None                     # opened when a reported issue is not in the records
    cards: dict[str, Any] = field(default_factory=dict)      # payments, proof, appealable: shown as cards
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

    def proactive(self, language: str = "hinglish") -> dict[str, Any]:
        """Paytm messages the merchant first: what it found and fixed, without being asked."""
        result = self.reply("", language if language in LANGUAGES else "hinglish", None, topics_override=["proactive"])
        result["proactive"] = True
        return result

    def transcribe(self, audio: bytes, content_type: str) -> dict[str, Any] | None:
        """The merchant's voice note as text (Sarvam speech-to-text); None when Sarvam is not configured."""
        client = self.backends.sarvam.client
        if not client.configured or not audio:
            return None
        try:
            out = client.transcribe(audio, content_type)
        except (*NETWORK_ERRORS, BackendError):
            return None
        code = out.get("language_code") or ""
        return {"text": out.get("transcript", "").strip(),
                "language": code if code in LANGUAGES and code != "hi-IN" else detect_language(out.get("transcript", ""))}

    def reply(self, question: str, language: str = "auto",
              history: list[dict[str, str]] | None = None, *, topics_override: list[str] | None = None,
              on_token=None) -> dict[str, Any]:
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
        topics, understood_by = (topics_override, "paytm") if topics_override else (_topics(question), "keywords")
        if not topics_override and topics[0] not in EXPLICIT and parse_payment_query(
                question, self.rt.clock.today().year):
            topics = ["payment"]
        if not topics_override and topics[0] not in EXPLICIT and topics != ["greet"] and len(question) > 3:
            # The model reads every message: it recognises the merchant's requests (appeal, proof, a person,
            # one payment) in any wording or script, and places what the keywords could not. For ordinary
            # subjects the keywords stay in charge; they were more accurate than a small model there.
            classified = self._classify(question)
            if classified and (classified in EXPLICIT or topics == ["summary"]):
                topics, understood_by = [classified], self._classifier_name()
        if topics[0] in EXPLICIT:
            topics = topics[:1]  # an explicit request is answered on its own
        self._question, self._history = question, history or []
        with self.lock:
            facts = self._facts(topics, question)
            system = self._system(language)
            # A greeting may not carry figures at all; anything else may repeat this merchant's real figures.
            known_paise = facts.paise if topics == ["greet"] else facts.paise | self._all_paise()
            not_returned = self._not_returned_paise()
        hinglish_draft = language in ("hinglish", "hi-IN")
        draft = " ".join(facts.draft_hi if hinglish_draft else facts.draft_en)

        source, note, text = "template", None, draft
        sarvam, local = self.backends.sarvam, self.backends.local
        grounding = " ".join(facts.lines) + " " + draft + " " + question
        require = topics[0] not in ("greet",)
        writers = []
        exact = topics[0] in ("payment", "proof", "handoff", "appeal")
        if exact:
            pass  # records and hand-offs are shown exactly; a model adds nothing but risk
        elif sarvam.client.configured:
            writers.append((sarvam, history))
        if not exact and local_writes_enabled() and local.available():
            writers.append((local, (history or [])[-4:]))  # a small model follows a short history better
        for writer, turns in writers:
            try:
                messages = self._messages(question, facts, draft, turns)
                if on_token is not None and hasattr(writer, "stream"):
                    # Shown to the merchant as unchecked while it is written; the guards below decide
                    # whether it stays.
                    candidate = writer.stream(system, messages, on_token)
                else:
                    candidate = writer.complete(system, messages)
            except (*NETWORK_ERRORS, BackendError) as exc:
                note = f"{writer.name} unavailable ({type(exc).__name__})"
                continue
            text, source, note = self._vet(candidate, draft, language, known_paise, writer.name, strict=False,
                                           grounding=grounding, require_primary=require, not_returned=not_returned)
            if source != "template":
                break
        if source == "template" and language not in ("hinglish", "en-IN"):
            translated = self._translate(draft, language)  # Sarvam's translation model, when configured
            if translated:
                text, source, note2 = self._vet(translated, draft, language, known_paise, "sarvam:mayura", strict=True)
                note = note2  # a successful translation supersedes an earlier rejection
            elif local.available():
                try:
                    candidate = local.complete(self._polish_system(language), self._polish_messages(question, draft))
                    text, source, note2 = self._vet(candidate, draft, language, known_paise, local.name, strict=True)
                    note = note2  # a successful translation supersedes an earlier rejection
                except (*NETWORK_ERRORS, BackendError) as exc:
                    note = f"{local.name} unavailable ({type(exc).__name__})"
            if source == "template":
                text = draft
        out = self._out(text, language, source, topics, facts, started, note, understood_by)
        if facts.ticket:
            out["ticket"] = facts.ticket
        for key in ("payments", "proof", "appealable"):
            if facts.cards.get(key) is not None:
                out[key] = facts.cards[key]
        if topics[0] in ("review", "proactive") or ("review" in topics):
            out["actions"] = self.review_cards(language)
        return out

    # -- the merchant resolves things from the chat -------------------------------------------------

    def review_cards(self, language: str = "hinglish") -> list[dict[str, Any]]:
        """Every case waiting for this merchant, as a card with the two decisions they can make."""
        hinglish = language in ("hinglish", "hi-IN", "auto")
        with self.lock:
            waiting = [c for c in self.rt.cases.in_state(CaseState.ESCALATED, merchant_id=self.merchant_id)
                       if not (c.escalation or {}).get("appeal")]
            cards = []
            for c in sorted(waiting, key=lambda c: -(c.proof.discrepancy_paise if c.proof else 0)):
                amount = c.proof.discrepancy_paise if c.proof else (c.escalation or {}).get("disputed_paise", 0)
                cards.append({
                    "case_id": c.case_id, "month": c.month, "amount_paise": amount, "pattern": c.pattern,
                    "title": SHORT_TITLE.get(c.pattern, (c.pattern, c.pattern))[1 if hinglish else 0],
                    "why": review_reason(c, hinglish),
                    "options": [
                        {"action": "file", "label": "Haan, correction file karo" if hinglish else "Yes, file the correction"},
                        {"action": "dismiss", "label": "Nahi, yeh charge sahi hai" if hinglish else "No, this charge is correct"},
                    ],
                })
        return cards

    def appeal(self, case_id: str, reason: str, language: str = "hinglish") -> dict[str, Any]:
        """The merchant disputes a case closed without recovery; the ops desk decides and replies here."""
        hinglish = language in ("hinglish", "hi-IN", "auto")
        with self.lock:
            case = self.rt.cases.get(case_id)
            if case.merchant_id != self.merchant_id:
                raise KeyError(case_id)
            merchant = self.rt.dataset.merchant(self.merchant_id).legal_name
            self.rt.appeal(case_id, reason, by=f"{merchant} (merchant, via chat)")
        text = (f"Aapki appeal ({case_id}) Paytm ki settlement team ko bhej di. Woh aapki baat dekhkar yahin faisla "
                "batayenge." if hinglish else
                f"Your appeal ({case_id}) has gone to Paytm's settlement team. They will review it and reply here.")
        return {"reply": text, "language": language, "source": "paytm", "case_id": case_id, "note": None,
                "cases": [], "topics": ["appeal"], "suggestions": SUGGESTIONS["summary"]}

    def decide(self, case_id: str, action: str, language: str = "hinglish") -> dict[str, Any]:
        """The merchant's decision on a review case, recorded with their name, then carried out."""
        hinglish = language in ("hinglish", "hi-IN", "auto")
        with self.lock:
            case = self.rt.cases.get(case_id)
            if case.merchant_id != self.merchant_id:
                raise KeyError(case_id)
            merchant = self.rt.dataset.merchant(self.merchant_id).legal_name
            self.rt.review(case_id, action, reviewer=f"{merchant} (merchant, via chat)",
                           note="Confirmed by the merchant in the Paytm Business chat")
            amount = case.claimed_paise or (case.proof.discrepancy_paise if case.proof else 0)
            claim = self.rt.followup.claims.get(case.claim_id) if case.claim_id else None
            state = str(case.state)
        if action == "file":
            ref = f" Reference {claim.reference}." if claim and claim.reference else ""
            text = (f"Ho gaya. Aapki confirmation par {inr(amount)} ki correction file kar di.{ref} Paisa aapke "
                    "account mein aate hi yahin bataayenge." if hinglish else
                    f"Done. The correction for {inr(amount)} is filed on your confirmation.{ref} We will tell you here "
                    "as soon as the money reaches your account.")
        else:
            text = ("Theek hai, humne yeh case band kar diya. Kuch file nahi hua." if hinglish else
                    "Understood. We closed this case; nothing was filed.")
        remaining = self.review_cards(language)
        return {"reply": text, "language": language, "source": "paytm", "case_id": case_id, "state": state,
                "claim_id": case.claim_id, "actions": remaining, "note": None, "cases": [], "topics": ["review"],
                "suggestions": SUGGESTIONS["review"] if remaining else SUGGESTIONS["summary"]}

    def _vet(self, candidate: str, draft: str, language: str, known_paise: set[int], name: str,
             *, strict: bool, grounding: str = "", require_primary: bool = False,
             not_returned: set[int] | None = None) -> tuple[str, str, str | None]:
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
            # Counts, days and dates must come from the facts too, not only rupee amounts.
            invented = numbers_in(candidate) - numbers_in(grounding) - {x.normalize() for x in allowed_amounts(known_paise)}
            if invented:
                return draft, "template", (f"{name} used the figure {', '.join(str(x) for x in sorted(invented))}, "
                                           "which is not in the records; the checked answer was used")
            if not amounts_in(draft) and amounts_in(candidate):
                return draft, "template", f"{name} added an amount the answer does not need; the checked answer was used"
            if not_returned and OVERCLAIM.search(candidate):
                return draft, "template", (f"{name} said everything is settled while money is still under review "
                                           "or in progress; the checked answer was used")
            misstated = claims_returned(candidate, not_returned or set())
            if misstated:
                return draft, "template", (f"{name} described {misstated} as returned, but it is still under review "
                                           "or in progress; the checked answer was used")
            primary = _primary_amount(draft)
            if require_primary and primary is not None and primary not in {a.normalize() for a in amounts_in(candidate)}:
                return draft, "template", f"{name} did not answer with the key figure; the checked answer was used"
        return candidate, name, None

    def _classifier_name(self) -> str:
        return self.backends.sarvam.name if self.backends.sarvam.client.configured else self.backends.local.name

    def _classify(self, question: str) -> str | None:
        """Ask a model which topic an unplaced message is about. The answer only picks which facts to read."""
        system = ("Classify a small Indian merchant's chat message about their Paytm settlements into one topic:\n"
                  + "\n".join(f"- {k}: {v}" for k, v in TOPIC_HELP.items())
                  + "\nExamples:\n"
                  + "\n".join(f'- "{q}" -> {t}' for q, t in CLASSIFIER_EXAMPLES)
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
            "7. The CHECKED DRAFT ANSWER is correct: say the same thing, in your own words, answering the "
            "merchant's actual message, and include its main rupee amount.\n"
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
            + (f"\n\nMERCHANT'S MESSAGE: {question}" if question else
               "\n\nThe merchant has not written anything: write Paytm's first message to them."))})
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

        if is_report(question) and topics[0] in TOPIC_PATTERNS and not self._groups(TOPIC_PATTERNS[topics[0]]):
            self._open_ticket(f, topics[0], question)
            return f

        case_match = re.search(r"case[-\s]?0*(\d+)", question, re.IGNORECASE)
        if case_match and topics[0] != "proof":
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

    # -- payment lookup ------------------------------------------------------------------------

    def _topic_payment(self, f: Facts, m: dict) -> None:
        rt = self.rt
        parsed = parse_payment_query(self._question, rt.clock.today().year) or (set(), None)
        amounts, day = parsed
        if not amounts and day is None:
            f.cards["payments"] = []
            f.draft_en.append("Which payment? Tell me the amount and the date, for example \"8 Sep, ₹2,113\", and I "
                              "will find it.")
            f.draft_hi.append("Kaunsa payment? Amount aur date batayiye, jaise \"8 Sep, ₹2,113\", main dhoondh dunga.")
            return
        view = rt.view(self.merchant_id)
        today = rt.observed_through()
        late = {b.txn_id: b for b in rt.sla_breaches.get(self.merchant_id, [])}
        pool = [t for t in view.transactions if str(t.kind) == "PAYMENT"]
        if day is not None:
            pool = [t for t in pool if abs((t.captured_at.date() - day).days) <= 1]
        if amounts:
            pool = [t for t in pool if any(abs(t.amount_paise - a) < 100 for a in amounts)]
        pool = sorted(pool, key=lambda t: t.captured_at, reverse=True)[:3]
        cards = []
        for t in pool:
            card = {"txn_id": t.txn_id, "amount_paise": t.amount_paise, "instrument": str(t.instrument),
                    "captured_at": t.captured_at.isoformat()}
            f.paise.add(t.amount_paise)
            line = next((ln for ln in view.lines_by_txn.get(t.txn_id, []) if str(ln.line_type) == "PAYMENT"), None)
            batch = view.batches_by_id.get(line.batch_id) if line else None
            if str(t.status) != "SUCCESS":
                card.update(status="FAILED", en="This payment failed, so nothing was charged or settled.",
                            hi="Yeh payment fail hua tha, isliye na paisa kata na settle hua.")
            elif batch is not None and batch.settlement_date <= today:
                f.paise.add(line.net_paise)
                card.update(status="LATE" if t.txn_id in late else "SETTLED", batch_id=batch.batch_id,
                            settled_on=batch.settlement_date.isoformat(), utr=batch.utr, net_paise=line.net_paise,
                            deductions_paise=line.gross_paise - line.net_paise)
                when = f"{batch.settlement_date:%d %b}"
                lag = f" ({late[t.txn_id].banking_days_late} banking days late; reported to the settlement team)" \
                    if t.txn_id in late else ""
                lag_hi = f" ({late[t.txn_id].banking_days_late} banking din der se; settlement team ko report kiya)" \
                    if t.txn_id in late else ""
                card.update(en=f"Settled on {when} in {batch.batch_id}: {inr(line.net_paise)} reached your bank "
                               f"(UTR {batch.utr}) after {inr(line.gross_paise - line.net_paise)} of charges{lag}.",
                            hi=f"{when} ko {batch.batch_id} mein settle hua: {inr(line.net_paise)} aapke bank mein aaya "
                               f"(UTR {batch.utr}), charges {inr(line.gross_paise - line.net_paise)}{lag_hi}.")
            else:
                due = rt.recon.deadline(view, t.captured_at)
                case = next((c for c in rt.cases.all(self.merchant_id) if t.txn_id in c.txn_ids
                             and c.component == "SETTLEMENT"), None)
                if due >= today and case is None:
                    card.update(status="NOT_DUE", due=due.isoformat(),
                                en=f"Not settled yet, and not late: it is due by {due:%d %b}.",
                                hi=f"Abhi settle nahi hua, par late bhi nahi: {due:%d %b} tak aana chahiye.")
                else:
                    state = str(case.state).replace("_", " ").lower() if case else "being checked"
                    card.update(status="MISSING", case_id=case.case_id if case else None,
                                en=f"It never reached a settlement. It is part of correction {case.case_id if case else ''} "
                                   f"({state}).", hi=f"Yeh kisi settlement mein nahi aaya. Iski correction "
                                   f"{case.case_id if case else ''} chal rahi hai ({state}).")
            cards.append(card)
        f.cards["payments"] = cards
        when = f"{day:%d %b} " if day else ""
        amt = f"{inr(min(amounts))} " if amounts else ""
        f.lines.append(f"Payment lookup for {when}{amt}: {len(cards)} match(es).")
        if not cards:
            f.draft_en.append(f"I could not find a {amt}payment {('on ' + when) if when else ''}in your records. Please "
                              "check the amount or the date and ask again.")
            f.draft_hi.append(f"Mujhe {when}{amt}ka koi payment nahi mila. Amount ya date check karke dobara poochiye.")
        elif len(cards) == 1:
            c = cards[0]
            f.draft_en.append(f"Your {inr(c['amount_paise'])} payment from {c['captured_at'][:10]}: {c['en']}")
            f.draft_hi.append(f"Aapka {inr(c['amount_paise'])} ka payment ({c['captured_at'][:10]}): {c['hi']}")
        else:
            f.draft_en.append(f"I found {len(cards)} payments that match. Each one is below with its status.")
            f.draft_hi.append(f"Mujhe {len(cards)} milte-julte payment mile. Har ek ka status neeche hai.")

    # -- proof ---------------------------------------------------------------------------------------

    def _topic_proof(self, f: Facts, m: dict) -> None:
        from mfp.runtime.views import case_detail

        rt = self.rt
        match = re.search(r"case[-\s]?0*(\d+)", self._question, re.IGNORECASE)
        mine = [c for c in rt.cases.all(self.merchant_id) if c.proof is not None]
        case = None
        if match:
            case = next((c for c in mine if c.case_id == f"CASE-{int(match.group(1)):05d}"), None)
        if case is None:
            case = next((c for c in sorted(mine, key=lambda c: c.case_id, reverse=True) if c.refund_credit), None) \
                or next((c for c in mine if c.proof.authorises_claim), None)
        if case is None:
            f.draft_en.append("There is no proven correction on your account yet, so there is no proof to show.")
            f.draft_hi.append("Aapke account par abhi koi proven correction nahi hai, isliye dikhane ko proof nahi hai.")
            return
        d = case_detail(rt, case.case_id)
        rule = (d["rules"] or [{}])[0]
        proof = d["proof"]
        credit = d.get("refund_credit")
        f.paise |= {proof["discrepancy_paise"], case.recovered_paise}
        f.cards["proof"] = {
            "case_id": case.case_id, "month": case.month, "title": SHORT_TITLE.get(case.pattern, (case.pattern,))[0],
            "payments": len(case.txn_ids), "overcharge_paise": proof["discrepancy_paise"],
            "verdict": proof["verdict"], "rule": rule.get("name"), "source": rule.get("source"),
            "example": (proof["computation"][0]["expression"] if proof["computation"] else None),
            "records": proof["records"], "reference": (d.get("claim") or {}).get("reference"),
            "state": str(case.state), "credit": credit,
        }
        paid = (f" {inr(case.recovered_paise)} came back on {credit['settlement_date']} (UTR {credit['utr']})."
                if credit else f" Status: {str(case.state).replace('_', ' ').lower()}.")
        paid_hi = (f" {inr(case.recovered_paise)} {credit['settlement_date']} ko wapas aaya (UTR {credit['utr']})."
                   if credit else f" Status: {str(case.state).replace('_', ' ').lower()}.")
        f.draft_en.append(f"Here is the proof for {case.case_id}: {len(case.txn_ids)} payments in {case.month} were "
                          f"overcharged by {inr(proof['discrepancy_paise'])}, recomputed from {rule.get('name')}."
                          f"{paid}")
        f.draft_hi.append(f"{case.case_id} ka proof: {case.month} mein {len(case.txn_ids)} payments par "
                          f"{inr(proof['discrepancy_paise'])} zyada kata, {rule.get('name')} ke hisaab se dobara "
                          f"calculate karke.{paid_hi}")

    # -- talk to a person ---------------------------------------------------------------------------------

    def _topic_handoff(self, f: Facts, m: dict) -> None:
        existing = self.rt.tickets.open_for(self.merchant_id, "handoff")
        if existing is not None:
            f.ticket = existing.summary()
            f.draft_en.append(f"You are already with our team on ticket {existing.ticket_id}. They will reply here.")
            f.draft_hi.append(f"Aapki baat pehle se hamari team ke paas hai (ticket {existing.ticket_id}). Woh yahin jawab denge.")
            return
        ticket = self.rt.tickets.open(self.merchant_id, "handoff", self._question,
                                      "The merchant asked to talk to a person.",
                                      transcript=[*self._history, {"role": "user", "content": self._question}])
        f.ticket = ticket.summary()
        f.draft_en.append(f"I have passed our conversation to Paytm's settlement team (ticket {ticket.ticket_id}). "
                          "A person will reply in this chat.")
        f.draft_hi.append(f"Maine hamari baatcheet Paytm ki settlement team ko de di hai (ticket {ticket.ticket_id}). "
                          "Ek insaan isi chat mein jawab dega.")

    # -- appeal -------------------------------------------------------------------------------------------

    def _topic_appeal(self, f: Facts, m: dict) -> None:
        closed = [c for c in self.rt.cases.all(self.merchant_id) if c.state is CaseState.CLOSED_UNRECOVERED]
        f.cards["appealable"] = [{
            "case_id": c.case_id, "month": c.month, "title": SHORT_TITLE.get(c.pattern, (c.pattern,))[0],
            "amount_paise": c.proof.discrepancy_paise if c.proof else c.detected_paise,
            "why_closed": next((t.reason for t in reversed(c.history) if t.to_state == "REJECTED"), "Closed without recovery"),
        } for c in closed]
        if not closed:
            f.draft_en.append("None of your cases was closed without recovery, so there is nothing to appeal.")
            f.draft_hi.append("Aapka koi case bina paisa wapas aaye band nahi hua, isliye appeal karne ko kuch nahi hai.")
            return
        f.draft_en.append(f"{len(closed)} case(s) closed without recovery. Pick one below and tell us why you "
                          "disagree; Paytm's team will decide and reply here.")
        f.draft_hi.append(f"{len(closed)} case bina paisa wapas aaye band hue. Neeche case chuniye aur batayiye aap "
                          "kyun sehmat nahi; Paytm team faisla karke yahin batayegi.")

    def _open_ticket(self, f: Facts, topic: str, question: str) -> None:
        """The merchant reports something the records do not show: hand it to people, and say so."""
        what = {"rental": "no device rental charged after a return or inside the free period",
                "charges": "no payment charge above the correct rate", "tax": "no GST, TCS or TDS deducted in error",
                "refund": "no refund debited twice", "missing": "every customer payment reached a settlement"}[topic]
        ticket = self.rt.tickets.open(self.merchant_id, topic, question, f"Records checked: {what}.")
        f.ticket = ticket.summary()
        f.lines.append(f"The merchant reports a {topic} problem. Paytm checked their records and found {what}. "
                       f"Ticket {ticket.ticket_id} was opened for Paytm's settlement team, who will reply in this chat. "
                       "Nothing is promised until the team confirms.")
        f.draft_en.append(f"I checked your records and could not find this yet: {what}. I have opened ticket "
                          f"{ticket.ticket_id} for our settlement team with your message, and they will reply here.")
        f.draft_hi.append(f"Maine aapke records check kiye, abhi yeh galti nahi dikhi. Aapka message ticket "
                          f"{ticket.ticket_id} ke saath hamari settlement team ko bhej diya hai, woh yahin jawab denge.")

    def _topic_proactive(self, f: Facts, m: dict) -> None:
        f.lines.append("Nobody asked: Paytm is messaging the merchant first to say what it found in their settlements "
                       "and what it has already fixed. Lead with the money back in their account.")
        if not m["identified_paise"]:
            f.draft_en.append("We checked all your settlements and everything is correct. Nothing was deducted in error.")
            f.draft_hi.append("Humne aapke saare settlements check kiye, sab sahi hai. Koi galat katoti nahi mili.")
            return
        approved = sum(c.approved_paise for c in self.rt.cases.in_state(CaseState.AWAITING_CREDIT,
                                                                           merchant_id=self.merchant_id))
        if approved:
            f.lines.append(f"{f.money(approved)} is approved by settlement ops and on its way; it is not in the "
                           "merchant's account yet.")
        if m["recovered_paise"]:
            f.draft_en.append(f"Good news: we checked every settlement and put {inr(m['recovered_paise'])} back into your "
                              f"account, out of {inr(m['identified_paise'])} that was deducted in error. You did not have "
                              "to raise a complaint.")
            f.draft_hi.append(f"Achhi khabar: humne aapke har settlement ko check kiya aur {inr(m['recovered_paise'])} "
                              f"aapke account mein wapas daal diya, kul {inr(m['identified_paise'])} galat kata tha. Aapko "
                              "koi complaint nahi karni padi.")
        else:
            f.draft_en.append(f"We checked every settlement and found {inr(m['identified_paise'])} deducted in error. "
                              "We have already filed the corrections; you did not have to raise a complaint.")
            f.draft_hi.append(f"Humne aapke har settlement ko check kiya aur {inr(m['identified_paise'])} galat kata hua "
                              "paaya. Correction hum file kar chuke hain; aapko koi complaint nahi karni padi.")
        if approved:
            f.draft_en.append(f"{inr(approved)} is already approved and will reach your account soon.")
            f.draft_hi.append(f"{inr(approved)} approve ho chuka hai aur jaldi aapke account mein aayega.")
        groups = self._groups(set().union(*TOPIC_PATTERNS.values()))
        fixed = sorted(((p, g) for p, g in groups.items() if g["recovered"]), key=lambda kv: -kv[1]["recovered"])
        for i, (pattern, g) in enumerate(fixed[:2]):
            f.lines.append(self._group_line(f, pattern, g))
            en, hi = WHY[pattern]
            f.draft_en.append(f"{('For example' if i == 0 else 'Also')}, {en}: {inr(g['recovered'])} returned.")
            f.draft_hi.append(f"{('Jaise' if i == 0 else 'Aur')}, {hi}: {inr(g['recovered'])} wapas.")
        if m["escalated"]:
            f.lines.append(f"{m['escalated']} case(s) worth {f.money(m['escalated_paise'])} wait for the merchant's review.")
            f.draft_en.append(f"{m['escalated']} case(s) need your decision; they are below.")
            f.draft_hi.append(f"{m['escalated']} case par aapka faisla chahiye, neeche dekhiye.")

    def _topic_greet(self, f: Facts, m: dict) -> None:
        f.lines.append("The merchant is greeting or thanking; reply warmly in one short sentence and offer more help. "
                       "Do not mention any amount.")
        f.draft_en.append("You're welcome! Ask me anything about your settlements, any time.")
        f.draft_hi.append("Aapka swagat hai! Settlement ke baare mein kabhi bhi kuch bhi poochiye.")

    def _not_returned_paise(self) -> set[int]:
        """Amounts that are NOT back with the merchant yet: under review, or still being corrected."""
        out: set[int] = set()
        if self.merchant_id not in self.rt.connected:
            return out
        m = self.rt.metrics(self.merchant_id)
        out |= {m["escalated_paise"], m["in_progress_paise"]} - {0}
        for c in self._cases():
            if c.state is CaseState.ESCALATED and c.proof:
                out.add(c.proof.discrepancy_paise)
        for g in self._groups(set().union(*TOPIC_PATTERNS.values())).values():
            if g["recovered"] < g["amount"]:
                out.add(g["amount"] - g["recovered"])
                if g["recovered"] == 0:
                    out.add(g["amount"])
        return out
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
        if not view.devices:
            # No device records: rental cannot have been checked, so do not say it was correct.
            f.lines.append("There are no device records for this merchant, so rental deductions were not checked.")
            f.draft_en.append("I don't have your device records, so I could not check rental deductions. If you think a "
                              "rental was charged wrongly, tell me and I will pass it to our team.")
            f.draft_hi.append("Mere paas aapke device ke records nahi hain, isliye rental ki katoti check nahi ho saki. "
                              "Agar aapko lagta hai rental galat kata, mujhe batayiye, main team ko bhej dunga.")
            return
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
        f.draft_en.append(f"{len(waiting)} case(s) worth {inr(total)} need your decision, because we could not "
                          "decide them ourselves. Each one is below: file the correction, or mark the charge correct.")
        f.draft_hi.append(f"{len(waiting)} case ({inr(total)}) par aapka faisla chahiye, kyunki hum inhe khud tay nahi "
                          "kar sakte. Har case neeche hai: correction file karein, ya charge sahi hai to bata dein.")

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
