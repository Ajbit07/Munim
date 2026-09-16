"""The observability spine.

Every autonomous action lands here. The log is append-only and hash-chained:
each event carries the hash of its predecessor, so a rewritten or deleted
event breaks the chain and verify() reports exactly where.

This is not decoration. It is what makes an autonomous financial action
auditable after the fact, and it powers the UI activity feed and the case
drill-down without a second source of truth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from mfp.core.clock import Clock
from mfp.core.enums import Actor

GENESIS_HASH = "0" * 64


class EventLogError(RuntimeError):
    """Raised on any attempt to mutate history, or on chain corruption."""


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    ts: str                  # ISO-8601, from the injected Clock
    actor: Actor
    kind: str                # e.g. "settlement.batch.observed", "case.state.changed"
    case_id: str | None
    merchant_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    hash: str = ""

    def compute_hash(self) -> str:
        body = {
            "seq": self.seq,
            "ts": self.ts,
            "actor": str(self.actor),
            "kind": self.kind,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, line: str) -> Event:
        raw = json.loads(line)
        raw["actor"] = Actor(raw["actor"])
        return cls(**raw)


class EventLog:
    """Append-only, hash-chained event log with an optional JSONL sink.

    There is deliberately no update() and no delete(). The absence of those
    methods is the guarantee.
    """

    def __init__(self, clock: Clock, sink: Path | None = None) -> None:
        self._clock = clock
        self._events: list[Event] = []
        self._sink = Path(sink) if sink else None
        if self._sink is not None:
            self._sink.parent.mkdir(parents=True, exist_ok=True)
            if self._sink.exists():
                self._load()

    # -- writing ---------------------------------------------------------
    def append(
        self,
        actor: Actor,
        kind: str,
        *,
        case_id: str | None = None,
        merchant_id: str | None = None,
        **payload: Any,
    ) -> Event:
        prev = self._events[-1].hash if self._events else GENESIS_HASH
        draft = Event(
            seq=len(self._events),
            ts=self._clock.now().isoformat(),
            actor=Actor(actor),
            kind=kind,
            case_id=case_id,
            merchant_id=merchant_id,
            payload=payload,
            prev_hash=prev,
        )
        event = Event(**{**asdict(draft), "actor": draft.actor, "hash": draft.compute_hash()})
        self._events.append(event)
        if self._sink is not None:
            with self._sink.open("a", encoding="utf-8") as handle:
                handle.write(event.to_json() + "\n")
        return event

    # -- reading ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[Event]:
        return iter(tuple(self._events))

    @property
    def events(self) -> tuple[Event, ...]:
        """A snapshot. Callers cannot reach the underlying list."""
        return tuple(self._events)

    def for_case(self, case_id: str) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.case_id == case_id)

    def for_merchant(self, merchant_id: str) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.merchant_id == merchant_id)

    def of_kind(self, kind: str) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.kind == kind)

    # -- integrity -------------------------------------------------------
    def verify(self) -> None:
        """Raise EventLogError naming the first event whose chain is broken."""
        expected_prev = GENESIS_HASH
        for index, event in enumerate(self._events):
            if event.seq != index:
                raise EventLogError(
                    f"Event at position {index} claims seq={event.seq}; "
                    "history has been reordered or an event was removed."
                )
            if event.prev_hash != expected_prev:
                raise EventLogError(
                    f"Event seq={event.seq} has prev_hash={event.prev_hash[:12]}..., "
                    f"expected {expected_prev[:12]}...; history was rewritten."
                )
            if event.hash != event.compute_hash():
                raise EventLogError(
                    f"Event seq={event.seq} ({event.kind}) hash mismatch; "
                    "its contents were modified after being appended."
                )
            expected_prev = event.hash

    def _load(self) -> None:
        assert self._sink is not None
        with self._sink.open("r", encoding="utf-8") as handle:
            self._events = [Event.from_json(line) for line in handle if line.strip()]
        self.verify()
