"""Creating refunds. The worker executes them (see ``handlers.PaymentHandlers.refund``)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.clock import Clock
from settle.core.errors import InvalidTransition, ValidationFailed
from settle.db.models import Booking, PaymentIntent, Refund, RefundSource, RefundStatus
from settle.domain.fees import split_refund
from settle.domain.money import Money
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic


def captured_intent(session: Session, booking: Booking) -> PaymentIntent:
    intent = session.scalar(select(PaymentIntent).where(PaymentIntent.booking_id == booking.id))
    if intent is None or intent.provider_capture_id is None:
        raise InvalidTransition("Nothing has been charged for this booking")
    return intent


def pending_refund_total(session: Session, booking_id: uuid.UUID) -> int:
    pending = session.scalars(
        select(Refund.amount_minor).where(
            Refund.booking_id == booking_id, Refund.status == RefundStatus.PENDING
        )
    ).all()
    return sum(pending)


def create_refund(
    session: Session,
    clock: Clock,
    *,
    booking: Booking,
    amount: Money,
    source: RefundSource,
    reason: str,
    dispute_id: uuid.UUID | None = None,
) -> Refund:
    """Record a refund and queue it. Funded proportionally from the host share and our fee."""
    intent = captured_intent(session, booking)
    remaining = intent.remaining_split
    still_refundable = remaining.gross - Money(
        pending_refund_total(session, booking.id), remaining.gross.currency
    )
    if not amount.is_positive or amount > still_refundable:
        raise ValidationFailed(
            f"Refund must be between {Money(1, amount.currency)} and {still_refundable}"
        )
    split = split_refund(remaining, amount)
    refund = Refund(
        booking_id=booking.id,
        source=source,
        dispute_id=dispute_id,
        amount_minor=split.total.amount,
        from_host_minor=split.from_host.amount,
        from_fee_minor=split.from_fee.amount,
        currency=split.total.currency,
        reason=reason,
    )
    session.add(refund)
    session.flush()
    enqueue(
        session,
        topic=Topic.REFUND_PAYMENT,
        aggregate_id=str(booking.id),
        payload={"refund_id": str(refund.id)},
        dedupe_key=f"{Topic.REFUND_PAYMENT.value}:{refund.id}",
        now=clock.now(),
    )
    return refund
