"""Chart of accounts for the double-entry ledger."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class EntryDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountKind(StrEnum):
    # Asset: money the platform holds at the payment provider.
    PROVIDER_CASH = "provider_cash"
    # Liability: money owed to a host, not yet paid out.
    HOST_PAYABLE = "host_payable"
    # Revenue: the platform's fee.
    PLATFORM_REVENUE = "platform_revenue"


# The side on which each kind of account grows. Balances are reported on that side.
NORMAL_SIDE: Final[dict[AccountKind, EntryDirection]] = {
    AccountKind.PROVIDER_CASH: EntryDirection.DEBIT,
    AccountKind.HOST_PAYABLE: EntryDirection.CREDIT,
    AccountKind.PLATFORM_REVENUE: EntryDirection.CREDIT,
}

PLATFORM_OWNER: Final = "platform"


@dataclass(frozen=True, slots=True)
class AccountRef:
    """Identifies a ledger account: what kind it is, who it belongs to, and its currency."""

    kind: AccountKind
    owner: str
    currency: str

    @classmethod
    def provider_cash(cls, currency: str) -> AccountRef:
        return cls(AccountKind.PROVIDER_CASH, PLATFORM_OWNER, currency)

    @classmethod
    def platform_revenue(cls, currency: str) -> AccountRef:
        return cls(AccountKind.PLATFORM_REVENUE, PLATFORM_OWNER, currency)

    @classmethod
    def host_payable(cls, host_id: str, currency: str) -> AccountRef:
        return cls(AccountKind.HOST_PAYABLE, host_id, currency)
