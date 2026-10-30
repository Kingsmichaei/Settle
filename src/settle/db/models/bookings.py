"""Bookings and their audit trail of status changes."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from settle.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from settle.db.enum_type import str_enum
from settle.db.models.identity import Listing
from settle.db.types import UTCDateTime
from settle.domain.money import Money
from settle.domain.states import BookingStatus


class Booking(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("check_out > check_in", name="valid_dates"),
        CheckConstraint("total_minor > 0", name="positive_total"),
        Index("ix_bookings_status", "status"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("listings.id"), nullable=False)
    guest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True, nullable=False
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True, nullable=False
    )
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    nights: Mapped[int] = mapped_column(Integer, nullable=False)
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        str_enum(BookingStatus, length=32), default=BookingStatus.PENDING, nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(String(200))
    # Optimistic locking: SQLAlchemy adds "WHERE version = :old" to every UPDATE, so two
    # workers can never both move the same booking.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    listing: Mapped[Listing] = relationship()
    history: Mapped[list[BookingStatusChange]] = relationship(
        back_populates="booking", order_by="BookingStatusChange.position"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def total(self) -> Money:
        return Money(self.total_minor, self.currency)

    @property
    def check_in_at(self) -> datetime:
        """Start of the check-in day, UTC. Holds and cancellation notice are measured from it."""
        return datetime.combine(self.check_in, time.min, tzinfo=UTC)


class BookingStatusChange(UUIDPrimaryKey, Base):
    """Append-only record of every status change, for audit and debugging."""

    __tablename__ = "booking_status_changes"
    __table_args__ = (UniqueConstraint("booking_id", "position"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[BookingStatus | None] = mapped_column(str_enum(BookingStatus))
    to_status: Mapped[BookingStatus] = mapped_column(str_enum(BookingStatus), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    booking: Mapped[Booking] = relationship(back_populates="history")
