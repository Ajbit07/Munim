"""Banking-day arithmetic for the settlement lifecycle.

Deliberately separate from the generator's copy: production must not import it.
Weekends and the configured national holidays are not banking days
(config/settlement_rules.json -> holiday_calendar).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def is_banking_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    return day.weekday() < 5 and day not in holidays


def add_banking_days(day: date, n: int, holidays: frozenset[date] = frozenset()) -> date:
    current, remaining = day, n
    while remaining > 0:
        current += timedelta(days=1)
        if is_banking_day(current, holidays):
            remaining -= 1
    return current


def banking_days_between(start: date, end: date, holidays: frozenset[date] = frozenset()) -> int:
    """Banking days after `start` up to and including `end`."""
    count, current = 0, start
    while current < end:
        current += timedelta(days=1)
        if is_banking_day(current, holidays):
            count += 1
    return count


def settlement_cycle(captured_at: datetime, cutoff: time) -> date:
    """A capture at or after the cutoff belongs to the next day's cycle."""
    if captured_at.timetz().replace(tzinfo=None) >= cutoff:
        return captured_at.date() + timedelta(days=1)
    return captured_at.date()
