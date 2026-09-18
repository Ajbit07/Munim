"""Language backends for the merchant chat, in order of preference.

  SarvamChat   Sarvam AI chat completions (SARVAM_API_KEY set)
  OllamaChat   a local model served by Ollama (default gemma3:4b); offline
  (none)       the assistant's deterministic answer is used as-is

A backend only phrases an answer. What the answer may say, and every rupee
figure in it, comes from the runtime's records; see assistant/chat.py.

Sarvam fields follow docs.sarvam.ai (checked Sep 2026): POST
/v1/chat/completions, /translate and /text-to-speech with the
api-subscription-key header. Not exercised against a live key in this build.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol

NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, json.JSONDecodeError)


class BackendError(RuntimeError):
    pass


class ChatBackend(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict[str, str]]) -> str: ...


def _post(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class SarvamClient:
    BASE = "https://api.sarvam.ai"

    def __init__(self, api_key: str | None = None, timeout: float = 20.0) -> None:
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        self.timeout = timeout
        self.chat_model = os.environ.get("MFP_SARVAM_MODEL", "sarvam-105b")
        self.tts_model = os.environ.get("MFP_SARVAM_TTS_MODEL", "bulbul:v2")
        self.speaker = os.environ.get("MFP_SARVAM_SPEAKER", "anushka")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def post(self, path: str, body: dict) -> dict:
        if not self.api_key:
            raise BackendError("SARVAM_API_KEY is not set")
        headers = {"api-subscription-key": self.api_key, "Authorization": f"Bearer {self.api_key}"}
        return _post(self.BASE + path, body, headers, self.timeout)

    def translate(self, text: str, target: str, *, source: str = "en-IN", mode: str = "formal") -> str:
        out = self.post("/translate", {"input": text, "source_language_code": source,
                                       "target_language_code": target, "mode": mode,
                                       "model": "mayura:v1", "numerals_format": "international"})
        return out["translated_text"]

    def speak(self, text: str, language: str) -> bytes:
        out = self.post("/text-to-speech", {"text": text[:1500], "language_code": language,
                                            "model": self.tts_model, "speaker": self.speaker,
                                            "output_audio_codec": "wav"})
        audios = out.get("audios") or []
        if not audios:
            raise BackendError("Sarvam returned no audio")
        return base64.b64decode(audios[0])


class SarvamChat:
    def __init__(self, client: SarvamClient | None = None) -> None:
        self.client = client or SarvamClient()
        self.name = f"sarvam:{self.client.chat_model}"

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        out = self.client.post("/v1/chat/completions", {
            "model": self.client.chat_model, "temperature": 0.2, "max_tokens": 400,
            "reasoning_effort": "low",
            "messages": [{"role": "system", "content": system}, *messages],
        })
        return out["choices"][0]["message"]["content"].strip()


class OllamaChat:
    def __init__(self, url: str | None = None, model: str | None = None, timeout: float = 60.0) -> None:
        self.url = (url or os.environ.get("MFP_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get("MFP_LOCAL_MODEL", "gemma3:4b")
        self.timeout = timeout
        self.name = f"local:{self.model}"
        self._checked_at = -1e9
        self._available = False

    def available(self) -> bool:
        """Is Ollama running with the model pulled? Re-checked at most every 30 seconds."""
        now = time.monotonic()
        if now - self._checked_at < 30:
            return self._available
        self._checked_at = now
        try:
            request = urllib.request.Request(self.url + "/api/tags")
            with urllib.request.urlopen(request, timeout=1.5) as response:
                tags = json.loads(response.read())
            names = {m.get("name", "") for m in tags.get("models", [])}
            self._available = any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names)
        except NETWORK_ERRORS:
            self._available = False
        return self._available

    def complete(self, system: str, messages: list[dict[str, str]], *, schema: dict | None = None,
                 max_tokens: int = 220) -> str:
        body = {
            "model": self.model, "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0.2, "seed": 7, "num_predict": max_tokens, "num_ctx": 4096,
                        "repeat_penalty": 1.1},
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if schema is not None:
            body["format"] = schema  # Ollama constrains the output to this JSON schema
        out = _post(self.url + "/api/chat", body, {}, self.timeout)
        return out["message"]["content"].strip()

    def warm(self) -> None:
        """Load the model into memory ahead of the first chat (the first load can take a minute)."""
        if self.available():
            try:
                _post(self.url + "/api/generate", {"model": self.model, "keep_alive": "30m"}, {}, 180)
            except NETWORK_ERRORS:
                pass


class BackendChain:
    """Sarvam when a key is configured, else the local model when it is running, else none."""

    def __init__(self, sarvam: SarvamChat | None = None, local: OllamaChat | None = None) -> None:
        self.sarvam = sarvam or SarvamChat()
        self.local = local or OllamaChat()

    def order(self) -> list[ChatBackend]:
        chain: list[ChatBackend] = []
        if self.sarvam.client.configured:
            chain.append(self.sarvam)
        if self.local.available():
            chain.append(self.local)
        return chain

    def status(self) -> dict:
        order = self.order()
        return {"active": order[0].name if order else "template",
                "sarvam_configured": self.sarvam.client.configured,
                "local_available": self.local.available(), "local_model": self.local.model,
                "voice": "sarvam" if self.sarvam.client.configured else "browser"}
