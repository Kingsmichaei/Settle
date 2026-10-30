"""Property tests: every accounting recipe balances for any amount and fee."""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from settle.core.errors import CurrencyMismatch, UnbalancedEntry, ValidationFailed
from settle.domain.fees import split_payment, split_refund
from settle.domain.money import Money
from settle.ledger.accounts import AccountRef, EntryDirection
from settle.ledger.posting import validate_lines
from settle.ledger.recipes import LineSpec, capture_lines, credit, debit, payout_lines, refund_lines

HOST = "host-1"
amounts = st.integers(min_value=1, max_value=10**12)
fees = st.integers(min_value=0, max_value=10_000)


def net_by_account(*entries: list[LineSpec]) -> Counter[AccountRef]:
    net: Counter[AccountRef] = Counter()
    for lines in entries:
        for line in lines:
            sign = 1 if line.direction is EntryDirection.DEBIT else -1
            net[line.account] += sign * line.money.amount
    return net


@given(amount=amounts, bps=fees)
def test_capture_balances(amount: int, bps: int) -> None:
    validate_lines(capture_lines(split_payment(Money(amount, "USD"), bps), HOST))


@given(amount=amounts, bps=fees)
def test_capture_then_full_refund_leaves_every_account_at_zero(amount: int, bps: int) -> None:
    split = split_payment(Money(amount, "USD"), bps)
    refund = refund_lines(split_refund(split, split.gross), HOST)
    validate_lines(refund)
    assert all(value == 0 for value in net_by_account(capture_lines(split, HOST), refund).values())


@given(data=st.data(), amount=amounts, bps=fees)
def test_partial_refund_balances_and_never_overdraws(
    data: st.DataObject, amount: int, bps: int
) -> None:
    split = split_payment(Money(amount, "USD"), bps)
    refunded = data.draw(st.integers(min_value=1, max_value=amount))
    refund = split_refund(split, Money(refunded, "USD"))
    validate_lines(refund_lines(refund, HOST))
    net = net_by_account(capture_lines(split, HOST), refund_lines(refund, HOST))
    # Nothing goes negative: we never owe a host less than zero or earn a negative fee.
    assert -net[AccountRef.host_payable(HOST, "USD")] >= 0
    assert -net[AccountRef.platform_revenue("USD")] >= 0
    assert net[AccountRef.provider_cash("USD")] == amount - refunded


@given(amount=amounts, bps=fees)
def test_capture_then_payout_leaves_only_the_fee(amount: int, bps: int) -> None:
    split = split_payment(Money(amount, "USD"), bps)
    payout = payout_lines(split.host_amount, HOST) if split.host_amount.is_positive else []
    net = net_by_account(capture_lines(split, HOST), payout)
    assert net[AccountRef.host_payable(HOST, "USD")] == 0
    assert net[AccountRef.provider_cash("USD")] == split.platform_fee.amount


def test_unbalanced_entry_is_refused() -> None:
    cash, revenue = AccountRef.provider_cash("USD"), AccountRef.platform_revenue("USD")
    with pytest.raises(UnbalancedEntry):
        validate_lines([debit(cash, Money(100, "USD")), credit(revenue, Money(99, "USD"))])


def test_single_line_entry_is_refused() -> None:
    with pytest.raises(UnbalancedEntry):
        validate_lines([debit(AccountRef.provider_cash("USD"), Money(100, "USD"))])


def test_mixed_currency_entry_is_refused() -> None:
    with pytest.raises(CurrencyMismatch):
        validate_lines(
            [
                debit(AccountRef.provider_cash("USD"), Money(100, "USD")),
                credit(AccountRef.platform_revenue("EUR"), Money(100, "EUR")),
            ]
        )


def test_non_positive_line_is_refused() -> None:
    cash, revenue = AccountRef.provider_cash("USD"), AccountRef.platform_revenue("USD")
    with pytest.raises(ValidationFailed):
        validate_lines([debit(cash, Money(0, "USD")), credit(revenue, Money(0, "USD"))])
