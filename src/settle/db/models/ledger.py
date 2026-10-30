"""Ledger tables. Rows are append-only: corrections are new, reversing entries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from settle.db.base import Base, UUIDPrimaryKey, utcnow
from settle.db.enum_type import str_enum
from settle.db.types import UTCDateTime
from settle.ledger.accounts import AccountKind, AccountRef, EntryDirection


class LedgerAccount(UUIDPrimaryKey, Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("kind", "owner", "currency"),)

    kind: Mapped[AccountKind] = mapped_column(str_enum(AccountKind), nullable=False)
    # "platform" for platform accounts, otherwise the owning user's id.
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    @property
    def ref(self) -> AccountRef:
        return AccountRef(self.kind, self.owner, self.currency)


class JournalEntry(UUIDPrimaryKey, Base):
    """One business event (a capture, a payout, a refund) recorded as balanced lines."""

    __tablename__ = "journal_entries"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Posting the same event twice returns the first entry instead of moving money again.
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bookings.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    lines: Mapped[list[LedgerLine]] = relationship(back_populates="entry")


class LedgerLine(UUIDPrimaryKey, Base):
    __tablename__ = "ledger_lines"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="positive_amount"),)

    entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journal_entries.id"), index=True, nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ledger_accounts.id"), index=True, nullable=False
    )
    direction: Mapped[EntryDirection] = mapped_column(str_enum(EntryDirection), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[LedgerAccount] = relationship()
