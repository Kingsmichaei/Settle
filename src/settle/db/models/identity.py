"""People and the things they list."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from settle.db.base import Base, Timestamps, UUIDPrimaryKey
from settle.db.enum_type import str_enum
from settle.domain.money import Money
from settle.domain.policies import CancellationPolicy


class UserRole(StrEnum):
    GUEST = "guest"
    HOST = "host"
    ADMIN = "admin"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # Hosts are paid in this currency; guests see it as their home currency.
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Where host payouts are sent (a PayPal account email).
    payout_email: Mapped[str | None] = mapped_column(String(320))


class Listing(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "listings"
    __table_args__ = (CheckConstraint("nightly_price_minor > 0", name="positive_price"),)

    host_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500))
    nightly_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cancellation_policy: Mapped[CancellationPolicy] = mapped_column(
        str_enum(CancellationPolicy), default=CancellationPolicy.MODERATE, nullable=False
    )

    host: Mapped[User] = relationship()

    @property
    def nightly_price(self) -> Money:
        return Money(self.nightly_price_minor, self.currency)
