"""The single door to a language model.

Modes (MFP_LLM_MODE):

  off      never call a model; callers use their deterministic path   (default)
  live     call Claude, use the answer, keep nothing
  record   call Claude and store each answer under fixtures/llm_cache/
  replay   answer only from fixtures/llm_cache/; a miss is an error

Answers are keyed by a hash of (model, system, user, schema), so a recorded
demo replays byte-identically with no network. That is how the reasoning layer
stays inside the offline-first rule.

Every answer is ADVISORY. Callers in this codebase use model output for
explanations, hypotheses and message interpretation -- never for amounts,
never for verdicts.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

MODEL = os.environ.get("MFP_LLM_MODEL", "claude-opus-5")
DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "fixtures" / "llm_cache"


class ReplayMiss(LookupError):
    """Replay mode found no recorded answer. Caught in rehearsal, not on stage."""


class LLMUnavailable(RuntimeError):
    pass


class LLMGateway:
    def __init__(self, mode: str | None = None, cache_dir: Path | None = None, model: str = MODEL) -> None:
        self.mode = (mode or os.environ.get("MFP_LLM_MODE", "off")).lower()
        if self.mode not in ("off", "live", "record", "replay"):
            raise ValueError(f"unknown MFP_LLM_MODE {self.mode!r}")
        self.cache_dir = cache_dir or DEFAULT_CACHE
        self.model = model
        self._client = None
        self.calls = 0
        self.cache_hits = 0

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _key(self, system: str, user: str, schema: dict) -> str:
        body = json.dumps({"model": self.model, "system": system, "user": user, "schema": schema},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _client_or_raise(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise LLMUnavailable("the anthropic package is not installed") from exc
            self._client = anthropic.Anthropic()
        return self._client

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], purpose: str) -> dict[str, Any] | None:
        """Return a schema-conforming dict, or None when the gateway is off."""
        if self.mode == "off":
            return None
        key = self._key(system, user, schema)
        path = self.cache_dir / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                raise ReplayMiss(f"no recorded answer for {purpose} ({key[:12]})")
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["answer"]

        answer = self._call(system, user, schema)
        self.calls += 1
        if self.mode == "record":
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"purpose": purpose, "model": self.model, "answer": answer},
                                       indent=2, sort_keys=True), encoding="utf-8")
        return answer

    def _call(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        import anthropic

        client = self._client_or_raise()
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                cache_control={"type": "ephemeral"},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable(f"rate limited: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("could not reach the Claude API") from exc

        if response.stop_reason == "refusal":
            raise LLMUnavailable("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailable("the answer was truncated")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMUnavailable("no text block in the response")
        return json.loads(text)
