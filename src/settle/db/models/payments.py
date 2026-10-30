"""Provider-facing money movements: charges, payouts and refunds."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from settle.db.base import Base, Timestamps, UUIDPrimaryKey
from settle.db.enum_type import str_enum
from settle.db.types import UTCDateTime
from settle.domain.fees import FeeSplit
from settle.domain.money import Money


class PaymentIntentStatus(StrEnum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    VOIDED = "voided"
    FAILED = "failed"


class PayoutStatus(StrEnum):
    SCHEDULED = "scheduled"  # waiting for its release time
    BLOCKED = "blocked"  # held back for review (risk, refused refund, chargeback)
    QUEUED = "queued"  # released; a worker will send it
    SENT = "sent"  # accepted by the provider; waiting for its confirmation webhook
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"  # nothing left to pay (refunded in full)


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RefundSource(StrEnum):
    CANCELLATION = "cancellation"
    DISPUTE = "dispute"


class PaymentIntent(UUIDPrimaryKey, Timestamps, Base):
    """A guest charge at the provider: order, then authorization, then capture."""

    __tablename__ = "payment_intents"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="positive_amount"),
        CheckConstraint("refunded_minor <= amount_minor", name="refund_within_amount"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentIntentStatus] = mapped_column(
        str_enum(PaymentIntentStatus), default=PaymentIntentStatus.CREATED, nullable=False
    )
    provider_order_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    approval_url: Mapped[str | None] = mapped_column(String(500))
    provider_authorization_id: Mapped[str | None] = mapped_column(String(128))
    provider_capture_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # Fee locked in at capture time, so a later fee change can't skew a refund.
    platform_fee_minor: Mapped[int | None] = mapped_column(Integer)
    refunded_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refunded_fee_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    @property
    def amount(self) -> Money:
        return Money(self.amount_minor, self.currency)

    @property
    def captured_split(self) -> FeeSplit:
        if self.platform_fee_minor is None:
            raise ValueError(f"Payment intent {self.id} has not been captured")
        fee = Money(self.platform_fee_minor, self.currency)
        return FeeSplit(gross=self.amount, platform_fee=fee, host_amount=self.amount - fee)

    @property
    def remaining_split(self) -> FeeSplit:
        """What is still held after refunds so far."""
        captured = self.captured_split
        refunded = Money(self.refunded_minor, self.currency)
        refunded_fee = Money(self.refunded_fee_minor, self.currency)
        return FeeSplit(
            gross=captured.gross - refunded,
            platform_fee=captured.platform_fee - refunded_fee,
            host_amount=captured.host_amount - (refunded - refunded_fee),
        )


class Payout(UUIDPrimaryKey, Timestamps, Base):
    """The host's share of a booking, held until ``release_at``."""

    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="non_negative_amount"),
        Index("ix_payouts_status_release_at", "status", "release_at"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), unique=True, nullable=False
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True, nullable=False
    )
    # What we owe, in the settlement (booking) currency. This is what the ledger records.
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PayoutStatus] = mapped_column(
        str_enum(PayoutStatus), default=PayoutStatus.SCHEDULED, nullable=False
    )
    release_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(300))
    # What the host receives, in their own currency, and the rate used (set when sent).
    payout_currency: Mapped[str | None] = mapped_column(String(3))
    payout_amount_minor: Mapped[int | None] = mapped_column(Integer)
    fx_rate: Mapped[str | None] = mapped_column(String(40))
    provider_payout_id: Mapped[str | None] = mapped_column(String(128), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Bumped after each definitive failure, so a retry is a new provider request, while
    # transient retries of the same attempt reuse its request id (and can't pay twice).
    send_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    @property
    def amount(self) -> Money:
        return Money(self.amount_minor, self.currency)

    @property
    def payout_amount(self) -> Money | None:
        if self.payout_amount_minor is None or self.payout_currency is None:
            return None
        return Money(self.payout_amount_minor, self.payout_currency)

    @property
    def request_id(self) -> str:
        """The provider idempotency key for the current send attempt."""
        return f"{self.id}:send:{self.send_attempt}"


class Refund(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="positive_amount"),
        CheckConstraint("from_host_minor + from_fee_minor = amount_minor", name="parts_add_up"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), index=True, nullable=False
    )
    source: Mapped[RefundSource] = mapped_column(str_enum(RefundSource), nullable=False)
    dispute_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("disputes.id"))
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    # How the refund is funded: from the host's share and from our fee.
    from_host_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    from_fee_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        str_enum(RefundStatus), default=RefundStatus.PENDING, nullable=False
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(128))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    @property
    def amount(self) -> Money:
        return Money(self.amount_minor, self.currency)

    @property
    def from_host(self) -> Money:
        return Money(self.from_host_minor, self.currency)

    @property
    def from_fee(self) -> Money:
        return Money(self.from_fee_minor, self.currency)
