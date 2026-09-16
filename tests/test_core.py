"""Block 0 foundations: money, rates, clock, event spine."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from mfp.core.clock import VirtualClock
from mfp.core.enums import Actor, RoundingPolicy
from mfp.core.events import EventLog, EventLogError
from mfp.core.money import Money, MoneyTypeError, Rate, apply_rate, sum_money


# -- money --------------------------------------------------------------


def test_money_rejects_float():
    with pytest.raises(MoneyTypeError):
        Money(12.5)
    with pytest.raises(MoneyTypeError):
        Money.from_rupees(12.5)


def test_money_rejects_bool():
    with pytest.raises(MoneyTypeError):
        Money(True)


def test_money_from_rupees_is_exact():
    assert Money.from_rupees("2000").paise == 200_000
    assert Money.from_rupees(Decimal("0.01")).paise == 1


def test_money_from_rupees_rejects_sub_paise():
    with pytest.raises(MoneyTypeError):
        Money.from_rupees(Decimal("1.005"))


def test_money_arithmetic_is_exact_over_many_additions():
    # The float trap: 0.01 summed 100 times is not 1.0 in binary floating point.
    total = sum_money(Money(1) for _ in range(100))
    assert total == Money.from_rupees("1")


def test_money_display():
    assert str(Money(200_000)) == "Rs 2,000.00"
    assert str(Money(-1)) == "-Rs 0.01"


# -- rates --------------------------------------------------------------


def test_rate_rejects_float():
    with pytest.raises(MoneyTypeError):
        Rate(0.004)
    with pytest.raises(MoneyTypeError):
        Rate.from_percent(0.4)


def test_npci_upi_mdr_worked_examples():
    """The published NPCI examples, effective 15 Oct 2026."""
    rate = Rate.from_percent("0.4")
    cap = Money(30_000)  # Rs 300

    def mdr(rupees: str) -> Money:
        raw = apply_rate(Money.from_rupees(rupees), rate, RoundingPolicy.HALF_UP)
        return min(raw, cap)

    assert mdr("3000") == Money.from_rupees("12")    # Rs 12 on Rs 3,000
    assert mdr("50000") == Money.from_rupees("200")  # Rs 200 on Rs 50,000
    assert mdr("100000") == Money.from_rupees("300") # capped, not Rs 400


def test_apply_rate_requires_explicit_rounding():
    with pytest.raises(TypeError):
        apply_rate(Money(100), Rate.from_percent("1"))  # type: ignore[call-arg]


def test_rounding_policy_actually_changes_the_answer():
    # Rs 1,234.58 at the RuPay-CC-on-UPI default band of 1.75% is 2160.515 paise.
    amount = Money(123_458)
    rate = Rate.from_percent("1.75")
    half_up = apply_rate(amount, rate, RoundingPolicy.HALF_UP)
    truncate = apply_rate(amount, rate, RoundingPolicy.TRUNCATE)
    assert half_up.paise == 2161
    assert truncate.paise == 2160
    # One paise per transaction is exactly the drift the materiality rule exists
    # to aggregate rather than discard.
    assert half_up != truncate


# -- clock --------------------------------------------------------------


def test_virtual_clock_advances():
    clock = VirtualClock("2026-09-16T10:00:00")
    start = clock.now()
    clock.advance_days(7)
    assert (clock.now() - start) == timedelta(days=7)


def test_virtual_clock_refuses_to_move_backwards():
    clock = VirtualClock("2026-09-16T10:00:00")
    with pytest.raises(ValueError):
        clock.advance(timedelta(days=-1))


def test_virtual_clock_crosses_the_upi_mdr_regime_boundary():
    """15 Oct 2026 is the boundary the whole L2 discrepancy class turns on."""
    clock = VirtualClock("2026-09-16T10:00:00")
    assert clock.today().isoformat() == "2026-09-16"
    clock.advance_days(29)
    assert clock.today().isoformat() == "2026-10-15"


# -- event spine --------------------------------------------------------


def test_event_log_appends_and_chains():
    log = EventLog(VirtualClock("2026-09-16T10:00:00"))
    log.append(Actor.MONITOR_AGENT, "settlement.batch.observed", batch_id="1842")
    log.append(Actor.INVESTIGATION_AGENT, "case.opened", case_id="C-1")
    assert len(log) == 2
    assert log.events[1].prev_hash == log.events[0].hash
    log.verify()


def test_event_log_has_no_mutation_api():
    """The guarantee is the absence of these methods, so assert their absence."""
    for forbidden in ("update", "delete", "remove", "insert", "clear"):
        assert not hasattr(EventLog, forbidden)


def test_event_log_snapshot_cannot_be_mutated_through():
    log = EventLog(VirtualClock("2026-09-16T10:00:00"))
    log.append(Actor.SYSTEM, "boot")
    snapshot = log.events
    assert isinstance(snapshot, tuple)
    assert len(log) == 1


def test_event_log_detects_tampering():
    log = EventLog(VirtualClock("2026-09-16T10:00:00"))
    log.append(Actor.PROOF_ENGINE, "proof.completed", case_id="C-1", amount_paise=218_000)
    log.append(Actor.FOLLOWUP_AGENT, "claim.filed", case_id="C-1")

    # Reach past the public API and rewrite history, as a bug or an attacker would.
    object.__setattr__(log._events[0], "payload", {"amount_paise": 999_999})

    with pytest.raises(EventLogError, match="hash mismatch"):
        log.verify()


def test_event_log_detects_removal():
    log = EventLog(VirtualClock("2026-09-16T10:00:00"))
    log.append(Actor.SYSTEM, "one")
    log.append(Actor.SYSTEM, "two")
    log.append(Actor.SYSTEM, "three")
    del log._events[1]
    with pytest.raises(EventLogError):
        log.verify()


def test_event_log_persists_and_reloads(tmp_path):
    sink = tmp_path / "events.jsonl"
    clock = VirtualClock("2026-09-16T10:00:00")
    log = EventLog(clock, sink=sink)
    log.append(Actor.MONITOR_AGENT, "backfill.started", merchant_id="M-1", months=12)
    clock.advance_days(1)
    log.append(Actor.MONITOR_AGENT, "backfill.completed", merchant_id="M-1")

    reloaded = EventLog(VirtualClock("2026-09-18T10:00:00"), sink=sink)
    assert len(reloaded) == 2
    assert reloaded.events[0].kind == "backfill.started"
    reloaded.verify()
