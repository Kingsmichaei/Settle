"""Currency conversion for host payouts.

The ledger always stays in the booking (settlement) currency. A payout in another currency is
recorded with the rate used, so every converted amount can be traced back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Protocol

from settle.core.errors import ValidationFailed
from settle.domain.money import Money, minor_unit_exponent


@dataclass(frozen=True, slots=True)
class FxQuote:
    source: Money
    target: Money
    rate: Decimal  # units of target currency per unit of source currency


class FxRates(Protocol):
    def rate(self, source: str, target: str) -> Decimal:
        """Units of ``target`` per unit of ``source``."""
        ...


class StaticFxRates:
    """Rates from configuration, e.g. ``{"USD:EUR": "0.92"}``. Inverse pairs are derived."""

    def __init__(self, rates: Mapping[str, str]) -> None:
        self._rates: dict[tuple[str, str], Decimal] = {}
        for pair, value in rates.items():
            source, _, target = pair.partition(":")
            try:
                rate = Decimal(value)
            except InvalidOperation as exc:
                raise ValidationFailed(f"Invalid FX rate for {pair}: {value!r}") from exc
            if rate <= 0 or not source or not target:
                raise ValidationFailed(f"Invalid FX rate entry {pair}={value}")
            self._rates[(source, target)] = rate
            self._rates.setdefault((target, source), 1 / rate)

    def rate(self, source: str, target: str) -> Decimal:
        if source == target:
            return Decimal(1)
        try:
            return self._rates[(source, target)]
        except KeyError:
            raise ValidationFailed(f"No FX rate configured for {source} to {target}") from None


def convert(amount: Money, target_currency: str, rates: FxRates) -> FxQuote:
    rate = rates.rate(amount.currency, target_currency)
    exact = amount.to_decimal() * rate
    exponent = minor_unit_exponent(target_currency)
    minor = (exact * (Decimal(10) ** exponent)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return FxQuote(source=amount, target=Money(int(minor), target_currency), rate=rate)
