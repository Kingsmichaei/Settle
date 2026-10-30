"""Writes balanced journal entries and reads balances."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from settle.core.errors import CurrencyMismatch, UnbalancedEntry, ValidationFailed
from settle.db.models import JournalEntry, LedgerAccount, LedgerLine
from settle.domain.money import Money
from settle.ledger.accounts import NORMAL_SIDE, AccountRef, EntryDirection
from settle.ledger.recipes import LineSpec

MIN_LINES_PER_ENTRY = 2


def validate_lines(lines: Sequence[LineSpec]) -> str:
    """Check an entry is well formed and balanced. Returns its currency."""
    if len(lines) < MIN_LINES_PER_ENTRY:
        raise UnbalancedEntry("A journal entry needs at least two lines")
    currencies = {line.money.currency for line in lines}
    if len(currencies) != 1:
        raise CurrencyMismatch("All lines in one entry must share a currency")
    for line in lines:
        if not line.money.is_positive:
            raise ValidationFailed("Ledger line amounts must be positive")
        if line.account.currency != line.money.currency:
            raise CurrencyMismatch("A line's amount must match its account's currency")
    debits = sum(line.money.amount for line in lines if line.direction is EntryDirection.DEBIT)
    credits = sum(line.money.amount for line in lines if line.direction is EntryDirection.CREDIT)
    if debits != credits:
        raise UnbalancedEntry(f"Debits ({debits}) do not equal credits ({credits})")
    return currencies.pop()


class Ledger:
    def __init__(self, session: Session) -> None:
        self._session = session

    def account(self, ref: AccountRef) -> LedgerAccount:
        """Get an account, creating it on first use."""
        existing = self._find_account(ref)
        if existing is not None:
            return existing
        try:
            with self._session.begin_nested():
                created = LedgerAccount(kind=ref.kind, owner=ref.owner, currency=ref.currency)
                self._session.add(created)
            return created
        except IntegrityError:
            # Another transaction created it first; use theirs.
            found = self._find_account(ref)
            if found is None:
                raise
            return found

    def post(
        self,
        *,
        event_type: str,
        idempotency_key: str,
        lines: Sequence[LineSpec],
        booking_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        """Record one balanced entry. Posting the same ``idempotency_key`` twice is a no-op."""
        validate_lines(lines)
        existing = self._session.scalar(
            select(JournalEntry).where(JournalEntry.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing

        entry = JournalEntry(
            event_type=event_type, idempotency_key=idempotency_key, booking_id=booking_id
        )
        self._session.add(entry)
        for spec in lines:
            self._session.add(
                LedgerLine(
                    entry=entry,
                    account=self.account(spec.account),
                    direction=spec.direction,
                    amount_minor=spec.money.amount,
                    currency=spec.money.currency,
                )
            )
        self._session.flush()
        return entry

    def balance(self, ref: AccountRef) -> Money:
        """Balance on the account's normal side (positive means the account has grown)."""
        account = self._find_account(ref)
        if account is None:
            return Money.zero(ref.currency)
        normal = NORMAL_SIDE[ref.kind]
        signed = case(
            (LedgerLine.direction == normal, LedgerLine.amount_minor),
            else_=-LedgerLine.amount_minor,
        )
        total = self._session.scalar(
            select(func.coalesce(func.sum(signed), 0)).where(LedgerLine.account_id == account.id)
        )
        return Money(int(total or 0), ref.currency)

    def unbalanced_entry_ids(self) -> list[uuid.UUID]:
        """Integrity check: ids of any entries whose debits and credits differ (should be none)."""
        signed = case(
            (LedgerLine.direction == EntryDirection.DEBIT, LedgerLine.amount_minor),
            else_=-LedgerLine.amount_minor,
        )
        rows = self._session.execute(
            select(LedgerLine.entry_id).group_by(LedgerLine.entry_id).having(func.sum(signed) != 0)
        )
        return [row.entry_id for row in rows]

    def _find_account(self, ref: AccountRef) -> LedgerAccount | None:
        return self._session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.kind == ref.kind,
                LedgerAccount.owner == ref.owner,
                LedgerAccount.currency == ref.currency,
            )
        )
