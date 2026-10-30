from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from settle.core.errors import CurrencyMismatch, ValidationFailed
from settle.domain.money import Money


def test_arithmetic_keeps_currency() -> None:
    assert Money(500, "USD") + Money(250, "USD") == Money(750, "USD")
    assert Money(500, "USD") - Money(750, "USD") == Money(-250, "USD")
    assert Money(125, "USD") * 4 == Money(500, "USD")
    assert -Money(10, "EUR") == Money(-10, "EUR")


def test_mixing_currencies_is_refused() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money(1, "USD") + Money(1, "EUR")
    with pytest.raises(CurrencyMismatch):
        _ = Money(1, "USD") < Money(1, "EUR")


@pytest.mark.parametrize("amount", [1.5, True, "10"])
def test_amount_must_be_integer_minor_units(amount: object) -> None:
    with pytest.raises(ValidationFailed):
        Money(amount, "USD")  # type: ignore[arg-type]


@pytest.mark.parametrize("currency", ["usd", "US", "USDX", "12$"])
def test_currency_must_be_iso_code(currency: str) -> None:
    with pytest.raises(ValidationFailed):
        Money(100, currency)


def test_from_decimal_respects_minor_units() -> None:
    assert Money.from_decimal("500.00", "USD") == Money(50_000, "USD")
    assert Money.from_decimal("500", "JPY") == Money(500, "JPY")
    assert Money.from_decimal(Decimal("1.2345"), "KWD") == Money(1_234, "KWD")


def test_display() -> None:
    assert str(Money(123_456, "USD")) == "USD 1,234.56"
    assert str(Money(1_500, "JPY")) == "JPY 1,500"


def test_ordering_within_a_currency() -> None:
    assert Money(1, "USD") < Money(2, "USD")
    assert max(Money(3, "USD"), Money(9, "USD")) == Money(9, "USD")


@given(amount=st.integers(min_value=0, max_value=10**12), bps=st.integers(0, 10_000))
def test_portion_never_exceeds_the_whole(amount: int, bps: int) -> None:
    whole = Money(amount, "USD")
    part = whole.portion_bps(bps)
    assert Money.zero("USD") <= part <= whole


def test_portion_rounds_half_to_even() -> None:
    assert Money(5, "USD").portion_bps(5_000) == Money(2, "USD")  # 2.5 -> 2
    assert Money(15, "USD").portion_bps(5_000) == Money(8, "USD")  # 7.5 -> 8
