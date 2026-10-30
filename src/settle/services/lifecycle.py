"""The single place where a booking's status changes."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from settle.db.models import Booking, BookingStatusChange
from settle.domain.states import BookingStatus, ensure_transition


def transition(session: Session, booking: Booking, target: BookingStatus, reason: str) -> None:
    """Move ``booking`` to ``target`` if the state machine allows it, and record the change."""
    ensure_transition(booking.status, target)
    record_change(session, booking, booking.status, target, reason)
    booking.status = target


def record_change(
    session: Session,
    booking: Booking,
    from_status: BookingStatus | None,
    to_status: BookingStatus,
    reason: str,
) -> None:
    # A per-booking sequence number orders the history exactly, even when timestamps tie.
    position = session.scalar(
        select(func.count())
        .select_from(BookingStatusChange)
        .where(BookingStatusChange.booking_id == booking.id)
    )
    session.add(
        BookingStatusChange(
            booking_id=booking.id,
            position=(position or 0) + 1,
            from_status=from_status,
            to_status=to_status,
            reason=reason[:300],
        )
    )
