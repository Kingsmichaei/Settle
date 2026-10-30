"""Money as integer minor units plus an ISO 4217 currency code.

Floats are never used for money. ``Money(50000, "USD")`` is $500.00.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from functools import total_ordering
from typing import Final

from settle.core.errors import CurrencyMismatch, ValidationFailed

# Currencies whose minor unit is not 1/100. Everything else defaults to 2 decimal places.
_MINOR_UNIT_EXPONENTS: Final[dict[str, int]] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
}

BPS_DENOMINATOR: Final = 10_000


def minor_unit_exponent(currency: str) -> int:
    return _MINOR_UNIT_EXPONENTS.get(currency, 2)


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise ValidationFailed("Money.amount must be an integer number of minor units")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValidationFailed(f"Invalid ISO 4217 currency code: {self.currency!r}")

    # ---- construction -------------------------------------------------------------------

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    @classmethod
    def from_decimal(cls, value: Decimal | str, currency: str) -> Money:
        """Build from a major-unit amount, e.g. ``Money.from_decimal("500.00", "USD")``."""
        exponent = minor_unit_exponent(currency)
        minor = (Decimal(value) * (Decimal(10) ** exponent)).quantize(
            Decimal(1), rounding=ROUND_HALF_EVEN
        )
        return cls(int(minor), currency)

    # ---- arithmetic ---------------------------------------------------------------------

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: int) -> Money:
        if isinstance(factor, bool) or not isinstance(factor, int):
            raise ValidationFailed("Money can only be multiplied by an integer")
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def portion_bps(self, bps: int) -> Money:
        """Return ``bps`` basis points of this amount, rounded half-to-even to a minor unit."""
        if not 0 <= bps <= BPS_DENOMINATOR:
            raise ValidationFailed("Basis points must be between 0 and 10000")
        exact = Decimal(self.amount) * bps / BPS_DENOMINATOR
        return Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)), self.currency)

    # ---- predicates and display ---------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    def to_decimal(self) -> Decimal:
        return Decimal(self.amount).scaleb(-minor_unit_exponent(self.currency))

    def __str__(self) -> str:
        exponent = minor_unit_exponent(self.currency)
        return f"{self.currency} {self.to_decimal():,.{exponent}f}"
