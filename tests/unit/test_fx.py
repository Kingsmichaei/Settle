from __future__ import annotations

from decimal import Decimal

import pytest

from settle.core.errors import ValidationFailed
from settle.domain.fx import StaticFxRates, convert
from settle.domain.money import Money

RATES = StaticFxRates({"USD:EUR": "0.92", "USD:JPY": "150"})


def test_converts_and_records_the_rate() -> None:
    quote = convert(Money(34_000, "USD"), "EUR", RATES)
    assert quote.target == Money(31_280, "EUR")
    assert quote.rate == Decimal("0.92")


def test_respects_target_minor_units() -> None:
    assert convert(Money(1_000, "USD"), "JPY", RATES).target == Money(1_500, "JPY")


def test_inverse_rate_is_derived() -> None:
    assert convert(Money(9_200, "EUR"), "USD", RATES).target == Money(10_000, "USD")


def test_same_currency_is_identity() -> None:
    quote = convert(Money(123, "USD"), "USD", RATES)
    assert quote.target == Money(123, "USD")
    assert quote.rate == 1


def test_missing_rate_is_an_error() -> None:
    with pytest.raises(ValidationFailed):
        convert(Money(100, "USD"), "GBP", RATES)


@pytest.mark.parametrize("bad", [{"USD:EUR": "zero"}, {"USD:EUR": "-1"}, {"USDEUR": "1"}])
def test_invalid_configuration_is_rejected(bad: dict[str, str]) -> None:
    with pytest.raises(ValidationFailed):
        StaticFxRates(bad)
