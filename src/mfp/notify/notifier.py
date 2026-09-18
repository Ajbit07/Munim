"""Merchant communication.

The merchant should not need to understand settlement mathematics. One or two
sentences, in the language they use, stating what was found, what came back,
and whether anything needs them.

  TemplatedNotifier  deterministic Hinglish and English templates; offline
  SarvamNotifier     Sarvam AI translation into Hindi (code-mixed) and
                     optional speech; falls back to the template on any failure

Sarvam endpoints and fields follow docs.sarvam.ai (checked Sep 2026:
/translate, /text-to-speech with text + language_code, header
api-subscription-key). NOT exercised against a live key in this build.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


def rupees(paise: int) -> str:
    value = Decimal(paise) / 100
    whole = int(value)
    s = str(whole)
    if len(s) > 3:  # Indian digit grouping: 12,34,567
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"₹{s}"


@dataclass
class MerchantMessage:
    language: str
    text: str
    english: str
    channel: str
    source: str
    audio_path: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    name: str

    def compose(self, facts: dict[str, Any]) -> MerchantMessage: ...


class TemplatedNotifier:
    name = "templated"

    def compose(self, facts: dict[str, Any]) -> MerchantMessage:
        identified, recovered = facts["identified_paise"], facts["recovered_paise"]
        pending, escalated = facts.get("in_progress_paise", 0), facts.get("escalated_cases", 0)
        period = facts.get("period", "Is mahine")
        if identified == 0:
            hinglish = f"{period} aapke settlements mein koi discrepancy nahi mili. Sab theek hai."
            english = f"{period}: no settlement discrepancy was found. Everything reconciles."
        else:
            hinglish = f"{period} {rupees(identified)} ki settlement discrepancy identify hui"
            english = f"{rupees(identified)} of settlement discrepancy was identified"
            if recovered >= identified:
                hinglish += " aur recover ho gayi."
                english += " and fully recovered."
            else:
                hinglish += f", jismein se {rupees(recovered)} recover ho gayi."
                english += f", of which {rupees(recovered)} has been recovered."
                if pending:
                    hinglish += f" {rupees(pending)} ki recovery chal rahi hai."
                    english += f" Recovery of {rupees(pending)} is in progress."
        if escalated:
            hinglish += f" {escalated} case aapke verification ke liye rakhe gaye hain."
            english += f" {escalated} case(s) are held for your verification."
        return MerchantMessage("hi-en", hinglish, english, "Paytm Business app", self.name, facts=facts)


class SarvamNotifier:
    BASE = "https://api.sarvam.ai"

    def __init__(self, api_key: str | None = None, audio_dir: Path | None = None, timeout: float = 8.0) -> None:
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY")
        self.template = TemplatedNotifier()
        self.audio_dir = audio_dir
        self.timeout = timeout
        self.name = "sarvam" if self.api_key else "templated (no SARVAM_API_KEY)"

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            self.BASE + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "api-subscription-key": self.api_key or ""})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def compose(self, facts: dict[str, Any]) -> MerchantMessage:
        base = self.template.compose(facts)
        if not self.api_key:
            return base
        try:
            translated = self._post("/translate", {
                "input": base.english, "source_language_code": "en-IN",
                "target_language_code": "hi-IN", "mode": "code-mixed",
            })
            text = translated.get("translated_text") or base.text
            audio_path = None
            if self.audio_dir is not None:
                speech = self._post("/text-to-speech", {"text": text, "language_code": "hi-IN",
                                                        "model": "bulbul:v2", "speaker": "anushka"})
                audios = speech.get("audios") or []
                if audios:
                    self.audio_dir.mkdir(parents=True, exist_ok=True)
                    path = self.audio_dir / "merchant_update.wav"
                    path.write_bytes(base64.b64decode(audios[0]))
                    audio_path = str(path)
            return MerchantMessage("hi-IN", text, base.english, "Paytm Business app", "sarvam", audio_path, facts)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            base.source = f"templated (sarvam failed: {type(exc).__name__})"
            return base
