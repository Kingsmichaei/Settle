"""The journal lines for each business event, as pure functions.

Keeping these free of the database makes the accounting rules easy to read and to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from settle.domain.fees import FeeSplit, RefundSplit
from settle.domain.money import Money
from settle.ledger.accounts import AccountRef, EntryDirection


@dataclass(frozen=True, slots=True)
class LineSpec:
    account: AccountRef
    direction: EntryDirection
    money: Money


def debit(account: AccountRef, money: Money) -> LineSpec:
    return LineSpec(account, EntryDirection.DEBIT, money)


def credit(account: AccountRef, money: Money) -> LineSpec:
    return LineSpec(account, EntryDirection.CREDIT, money)


def capture_lines(split: FeeSplit, host_id: str) -> list[LineSpec]:
    """Guest money arrives at the provider; we owe the host their share and keep the fee."""
    currency = split.gross.currency
    lines = [debit(AccountRef.provider_cash(currency), split.gross)]
    if split.host_amount.is_positive:
        lines.append(credit(AccountRef.host_payable(host_id, currency), split.host_amount))
    if split.platform_fee.is_positive:
        lines.append(credit(AccountRef.platform_revenue(currency), split.platform_fee))
    return lines


def payout_lines(amount: Money, host_id: str) -> list[LineSpec]:
    """Money leaves the provider for the host; we no longer owe it."""
    return [
        debit(AccountRef.host_payable(host_id, amount.currency), amount),
        credit(AccountRef.provider_cash(amount.currency), amount),
    ]


def refund_lines(refund: RefundSplit, host_id: str) -> list[LineSpec]:
    """Money goes back to the guest, funded from the host's share and from our fee."""
    currency = refund.total.currency
    lines = [credit(AccountRef.provider_cash(currency), refund.total)]
    if refund.from_host.is_positive:
        lines.append(debit(AccountRef.host_payable(host_id, currency), refund.from_host))
    if refund.from_fee.is_positive:
        lines.append(debit(AccountRef.platform_revenue(currency), refund.from_fee))
    return lines
