from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from settle.core.errors import ValidationFailed
from settle.domain.fees import split_payment, split_refund
from settle.domain.money import Money


@given(amount=st.integers(min_value=1, max_value=10**12), bps=st.integers(0, 10_000))
def test_fee_and_host_share_always_add_up_to_the_payment(amount: int, bps: int) -> None:
    split = split_payment(Money(amount, "USD"), bps)
    assert split.platform_fee + split.host_amount == split.gross
    assert split.host_amount.amount >= 0


def test_standard_fee() -> None:
    split = split_payment(Money(50_000, "USD"), 1_500)
    assert split.platform_fee == Money(7_500, "USD")
    assert split.host_amount == Money(42_500, "USD")


@pytest.mark.parametrize("amount", [0, -100])
def test_payment_must_be_positive(amount: int) -> None:
    with pytest.raises(ValidationFailed):
        split_payment(Money(amount, "USD"), 1_500)


@given(data=st.data(), amount=st.integers(min_value=1, max_value=10**10), bps=st.integers(0, 9_999))
def test_refund_parts_add_up_and_stay_within_each_share(
    data: st.DataObject, amount: int, bps: int
) -> None:
    payment = split_payment(Money(amount, "USD"), bps)
    refund = Money(data.draw(st.integers(min_value=1, max_value=amount)), "USD")
    parts = split_refund(payment, refund)
    assert parts.from_host + parts.from_fee == refund
    assert Money.zero("USD") <= parts.from_host <= payment.host_amount
    assert Money.zero("USD") <= parts.from_fee <= payment.platform_fee


def test_refund_is_proportional() -> None:
    payment = split_payment(Money(50_000, "USD"), 1_500)  # host 42,500, fee 7,500
    parts = split_refund(payment, Money(10_000, "USD"))  # 20% of the payment
    assert parts.from_fee == Money(1_500, "USD")
    assert parts.from_host == Money(8_500, "USD")


@pytest.mark.parametrize("refund", [0, 50_001])
def test_refund_must_be_within_the_payment(refund: int) -> None:
    with pytest.raises(ValidationFailed):
        split_refund(split_payment(Money(50_000, "USD"), 1_500), Money(refund, "USD"))
