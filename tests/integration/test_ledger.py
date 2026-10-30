from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from settle.db.models import JournalEntry, LedgerAccount
from settle.domain.money import Money
from settle.ledger.accounts import AccountRef
from settle.ledger.posting import Ledger
from settle.ledger.recipes import credit, debit

CASH = AccountRef.provider_cash("USD")
REVENUE = AccountRef.platform_revenue("USD")
HOST = AccountRef.host_payable("host-1", "USD")


def post_sale(ledger: Ledger, key: str = "sale-1") -> JournalEntry:
    return ledger.post(
        event_type="test.sale",
        idempotency_key=key,
        lines=[
            debit(CASH, Money(1_000, "USD")),
            credit(HOST, Money(850, "USD")),
            credit(REVENUE, Money(150, "USD")),
        ],
    )


def test_balances_are_reported_on_each_accounts_normal_side(session: Session) -> None:
    ledger = Ledger(session)
    post_sale(ledger)
    assert ledger.balance(CASH) == Money(1_000, "USD")
    assert ledger.balance(HOST) == Money(850, "USD")
    assert ledger.balance(REVENUE) == Money(150, "USD")


def test_posting_the_same_key_twice_records_one_entry(session: Session) -> None:
    ledger = Ledger(session)
    first = post_sale(ledger)
    second = post_sale(ledger)
    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(JournalEntry)) == 1
    assert ledger.balance(CASH) == Money(1_000, "USD")


def test_accounts_are_created_once(session: Session) -> None:
    ledger = Ledger(session)
    post_sale(ledger, "sale-1")
    post_sale(ledger, "sale-2")
    assert session.scalar(select(func.count()).select_from(LedgerAccount)) == 3


def test_unknown_account_has_zero_balance(session: Session) -> None:
    assert Ledger(session).balance(AccountRef.host_payable("nobody", "EUR")) == Money(0, "EUR")


def test_integrity_check_passes_for_valid_entries(session: Session) -> None:
    ledger = Ledger(session)
    post_sale(ledger)
    assert ledger.unbalanced_entry_ids() == []
