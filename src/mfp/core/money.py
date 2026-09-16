"""Money and rates.

Two rules govern every financial value in this system:

1. Money is an integer number of paise. Never a float, never a Decimal rupee.
2. Rates are Decimal. Turning a rate into a money amount always requires an
   explicit RoundingPolicy -- there is no default, because which policy applies
   is itself a configured financial rule (fee_rules.json -> rounding_rule_id).
   Guessing it is how rounding drift becomes invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Final

from mfp.core.enums import RoundingPolicy

PAISE_PER_RUPEE: Final = 100

_ROUNDING_MAP: Final = {
    RoundingPolicy.HALF_UP: ROUND_HALF_UP,
    RoundingPolicy.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingPolicy.TRUNCATE: ROUND_DOWN,
}


class MoneyTypeError(TypeError):
    """Raised when a float or other unsafe type reaches a money constructor."""


@dataclass(frozen=True, order=True, slots=True)
class Money:
    """An exact amount in integer paise."""

    paise: int

    def __post_init__(self) -> None:
        # bool is an int subclass; exclude it explicitly.
        if isinstance(self.paise, bool) or not isinstance(self.paise, int):
            raise MoneyTypeError(
                f"Money requires int paise, got {type(self.paise).__name__}. "
                "Floats are never permitted in the financial path."
            )

    # -- constructors ----------------------------------------------------
    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    @classmethod
    def from_rupees(cls, rupees: str | int | Decimal) -> Money:
        """Build from a rupee value. Strings, ints and Decimals only."""
        if isinstance(rupees, float):
            raise MoneyTypeError(
                "Money.from_rupees does not accept float; pass str or Decimal."
            )
        value = Decimal(rupees) * PAISE_PER_RUPEE
        if value != value.to_integral_value():
            raise MoneyTypeError(
                f"{rupees} rupees is not a whole number of paise; "
                "round explicitly with apply_rate() instead."
            )
        return cls(int(value))

    # -- arithmetic ------------------------------------------------------
    def __add__(self, other: Money) -> Money:
        return Money(self.paise + _as_money(other).paise)

    def __sub__(self, other: Money) -> Money:
        return Money(self.paise - _as_money(other).paise)

    def __neg__(self) -> Money:
        return Money(-self.paise)

    def __abs__(self) -> Money:
        return Money(abs(self.paise))

    def __mul__(self, count: int) -> Money:
        if isinstance(count, bool) or not isinstance(count, int):
            raise MoneyTypeError("Money may only be multiplied by an int count.")
        return Money(self.paise * count)

    # -- predicates ------------------------------------------------------
    @property
    def is_zero(self) -> bool:
        return self.paise == 0

    @property
    def is_positive(self) -> bool:
        return self.paise > 0

    # -- presentation ----------------------------------------------------
    @property
    def rupees(self) -> Decimal:
        """Exact rupee value. For display and reporting only."""
        return Decimal(self.paise) / PAISE_PER_RUPEE

    def __str__(self) -> str:
        sign = "-" if self.paise < 0 else ""
        whole, part = divmod(abs(self.paise), PAISE_PER_RUPEE)
        return f"{sign}Rs {whole:,}.{part:02d}"

    def __repr__(self) -> str:
        return f"Money({self.paise})"


def _as_money(value: object) -> Money:
    if not isinstance(value, Money):
        raise MoneyTypeError(f"Expected Money, got {type(value).__name__}")
    return value


def sum_money(amounts) -> Money:
    """Sum an iterable of Money. Empty sums to zero."""
    total = 0
    for item in amounts:
        total += _as_money(item).paise
    return Money(total)


@dataclass(frozen=True, slots=True)
class Rate:
    """A rate as a Decimal fraction. 0.4 percent is Rate(Decimal("0.004"))."""

    value: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.value, float):
            raise MoneyTypeError("Rate requires Decimal, not float.")
        if not isinstance(self.value, Decimal):
            raise MoneyTypeError(
                f"Rate requires Decimal, got {type(self.value).__name__}"
            )

    @classmethod
    def from_percent(cls, percent: str | int | Decimal) -> Rate:
        if isinstance(percent, float):
            raise MoneyTypeError("Rate.from_percent does not accept float.")
        return cls(Decimal(percent) / Decimal(100))

    @classmethod
    def zero(cls) -> Rate:
        return cls(Decimal(0))

    @property
    def as_percent(self) -> Decimal:
        return self.value * 100

    @property
    def is_zero(self) -> bool:
        return self.value == 0

    def __str__(self) -> str:
        return f"{self.as_percent.normalize()}%"


def apply_rate(amount: Money, rate: Rate, rounding: RoundingPolicy) -> Money:
    """Apply a rate to an amount, rounding to whole paise by an explicit policy.

    The rounding policy is a required argument by design. See module docstring.
    """
    if not isinstance(rounding, RoundingPolicy):
        raise MoneyTypeError(
            "apply_rate requires an explicit RoundingPolicy; there is no default."
        )
    exact = Decimal(amount.paise) * rate.value
    return Money(int(exact.quantize(Decimal(1), rounding=_ROUNDING_MAP[rounding])))
