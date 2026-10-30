"""A host's earnings picture: balances, payouts, and the context the AI assistant answers from."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.ai.host_assistant import HostContext, PayoutView
from settle.db.models import Booking, LedgerAccount, Listing, Payout, PayoutStatus, User
from settle.domain.money import Money
from settle.ledger.accounts import AccountKind, AccountRef
from settle.ledger.posting import Ledger

_UPCOMING = (PayoutStatus.SCHEDULED, PayoutStatus.BLOCKED, PayoutStatus.QUEUED, PayoutStatus.SENT)


@dataclass(frozen=True, slots=True)
class HostPayoutRow:
    payout: Payout
    booking: Booking
    listing: Listing


def host_balances(session: Session, host: User) -> list[Money]:
    """What the platform owes this host, per currency, straight from the ledger."""
    currencies = session.scalars(
        select(LedgerAccount.currency).where(
            LedgerAccount.kind == AccountKind.HOST_PAYABLE, LedgerAccount.owner == str(host.id)
        )
    ).all()
    ledger = Ledger(session)
    return [ledger.balance(AccountRef.host_payable(str(host.id), c)) for c in sorted(currencies)]


def host_payouts(session: Session, host: User, *, limit: int = 100) -> list[HostPayoutRow]:
    rows = session.execute(
        select(Payout, Booking, Listing)
        .join(Booking, Booking.id == Payout.booking_id)
        .join(Listing, Listing.id == Booking.listing_id)
        .where(Payout.host_id == host.id)
        .order_by(Payout.release_at.desc())
        .limit(limit)
    ).all()
    return [HostPayoutRow(payout, booking, listing) for payout, booking, listing in rows]


def _view(row: HostPayoutRow) -> PayoutView:
    received = row.payout.payout_amount
    return PayoutView(
        booking_ref=str(row.booking.id)[:8],
        listing=row.listing.title,
        amount=str(row.payout.amount),
        status=row.payout.status.value,
        release_at=row.payout.release_at.isoformat(),
        host_receives=str(received)
        if received and received.currency != row.payout.currency
        else None,
        failure_reason=row.payout.failure_reason or row.payout.review_reason,
    )


def assistant_context(session: Session, host: User) -> HostContext:
    rows = host_payouts(session, host)
    upcoming = sorted(
        (row for row in rows if row.payout.status in _UPCOMING), key=lambda r: r.payout.release_at
    )
    return HostContext(
        host_name=host.full_name,
        owed=[str(balance) for balance in host_balances(session, host)],
        upcoming=[_view(row) for row in upcoming],
        recent_paid=[_view(row) for row in rows if row.payout.status is PayoutStatus.PAID][:5],
        failed=[_view(row) for row in rows if row.payout.status is PayoutStatus.FAILED],
    )
