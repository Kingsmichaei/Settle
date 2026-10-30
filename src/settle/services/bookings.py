"""Creating, finding and listing bookings."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.errors import InvalidTransition, NotFound, ValidationFailed
from settle.db.models import Booking, Listing, User, UserRole
from settle.domain.states import BookingStatus
from settle.services.lifecycle import record_change

MAX_NIGHTS = 365
MAX_PAGE = 200

# Bookings in these states no longer occupy the listing's calendar.
_INACTIVE = (BookingStatus.VOIDED, BookingStatus.REFUNDED)


def get_booking(session: Session, booking_id: uuid.UUID) -> Booking:
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise NotFound(f"Booking {booking_id} not found")
    return booking


def list_bookings(
    session: Session, user: User, *, status: BookingStatus | None = None, limit: int = 50
) -> Sequence[Booking]:
    """Newest first. Guests see their trips, hosts their reservations, admins everything."""
    query = select(Booking).order_by(Booking.created_at.desc()).limit(min(limit, MAX_PAGE))
    if user.role is UserRole.GUEST:
        query = query.where(Booking.guest_id == user.id)
    elif user.role is UserRole.HOST:
        query = query.where(Booking.host_id == user.id)
    if status is not None:
        query = query.where(Booking.status == status)
    return session.scalars(query).all()


def create_booking(
    session: Session,
    *,
    guest_id: uuid.UUID,
    listing_id: uuid.UUID,
    check_in: date,
    check_out: date,
    today: date,
) -> Booking:
    nights = (check_out - check_in).days
    if nights <= 0:
        raise ValidationFailed("check_out must be after check_in")
    if nights > MAX_NIGHTS:
        raise ValidationFailed(f"A booking can be at most {MAX_NIGHTS} nights")
    if check_in < today:
        raise ValidationFailed("check_in cannot be in the past")

    guest = session.get(User, guest_id)
    if guest is None or guest.role is not UserRole.GUEST:
        raise NotFound(f"Guest {guest_id} not found")
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise NotFound(f"Listing {listing_id} not found")
    if _overlaps_existing(session, listing.id, check_in, check_out):
        raise InvalidTransition("The listing is already booked for some of those nights")

    total = listing.nightly_price * nights
    booking = Booking(
        listing_id=listing.id,
        guest_id=guest.id,
        host_id=listing.host_id,
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        total_minor=total.amount,
        currency=total.currency,
        status=BookingStatus.PENDING,
    )
    session.add(booking)
    session.flush()
    record_change(session, booking, None, BookingStatus.PENDING, "booking created")
    return booking


def _overlaps_existing(
    session: Session, listing_id: uuid.UUID, check_in: date, check_out: date
) -> bool:
    clash = session.scalar(
        select(Booking.id)
        .where(
            Booking.listing_id == listing_id,
            Booking.status.not_in(_INACTIVE),
            Booking.check_in < check_out,
            Booking.check_out > check_in,
        )
        .limit(1)
    )
    return clash is not None
