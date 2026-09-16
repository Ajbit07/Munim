"""Time.

Nothing in this system may call datetime.now() directly. Every component takes
a Clock. This exists so that:

  - the 12-month backfill can walk a year of history in seconds,
  - the Follow-up Agent's "wait 7 days, then chase" is demonstrable live,
  - tests control settlement-cycle boundaries exactly,
  - a seeded run is reproducible end to end.

Retrofitting this later is expensive, so it lands in Block 0.
tools/check_firewall.py enforces the no-datetime.now() rule statically.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


@runtime_checkable
class Clock(Protocol):
    """The only sanctioned source of the current time."""

    def now(self) -> datetime: ...

    def today(self) -> date: ...


class SystemClock:
    """Wall-clock time. Permitted only at process edges, never in the core."""

    def now(self) -> datetime:
        return datetime.now(tz=IST)

    def today(self) -> date:
        return self.now().date()

    def __repr__(self) -> str:
        return "SystemClock()"


class VirtualClock:
    """A controllable clock. The default clock for agents, tests and the demo."""

    def __init__(self, start: datetime | str) -> None:
        self._now = _coerce(start)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    # -- control ---------------------------------------------------------
    def advance(self, delta: timedelta) -> datetime:
        """Move forward. Time never moves backwards on a VirtualClock."""
        if delta < timedelta(0):
            raise ValueError("VirtualClock cannot move backwards; use set_to().")
        self._now = self._now + delta
        return self._now

    def advance_days(self, days: int) -> datetime:
        return self.advance(timedelta(days=days))

    def set_to(self, moment: datetime | str) -> datetime:
        """Jump to an absolute moment. For scenario setup only."""
        self._now = _coerce(moment)
        return self._now

    def __repr__(self) -> str:
        return f"VirtualClock({self._now.isoformat()})"


def _coerce(moment: datetime | str) -> datetime:
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if not isinstance(moment, datetime):
        raise TypeError(f"Expected datetime or ISO string, got {type(moment).__name__}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment
