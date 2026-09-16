"""Banking-day arithmetic for the settlement lifecycle.

Deliberately separate from the generator's copy: production must not import it.
Weekends only, per config/settlement_rules.json -> holiday_calendar (ASSUMED).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def is_banking_day(day: date) -> bool:
    return day.weekday() < 5


def add_banking_days(day: date, n: int) -> date:
    current, remaining = day, n
    while remaining > 0:
        current += timedelta(days=1)
        if is_banking_day(current):
            remaining -= 1
    return current


def settlement_cycle(captured_at: datetime, cutoff: time) -> date:
    """A capture at or after the cutoff belongs to the next day's cycle."""
    if captured_at.timetz().replace(tzinfo=None) >= cutoff:
        return captured_at.date() + timedelta(days=1)
    return captured_at.date()
