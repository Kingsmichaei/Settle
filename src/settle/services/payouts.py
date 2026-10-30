"""Releasing held funds to hosts, completing and failing payouts, and operator actions."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from settle.core.clock import Clock
from settle.core.errors import InvalidTransition
from settle.db.models import Booking, Payout, PayoutStatus, Refund, RefundStatus, RiskAssessment
from settle.domain.states import RELEASABLE_STATES, BookingStatus
from settle.ledger.posting import Ledger
from settle.ledger.recipes import payout_lines
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic
from settle.services.lifecycle import transition

logger = logging.getLogger(__name__)


def payout_for(session: Session, booking: Booking) -> Payout | None:
    return session.scalar(select(Payout).where(Payout.booking_id == booking.id))


def queue_send(session: Session, payout: Payout, clock: Clock) -> None:
    enqueue(
        session,
        topic=Topic.SEND_PAYOUT,
        aggregate_id=str(payout.booking_id),
        payload={"payout_id": str(payout.id)},
        # One outbox command per send attempt; a retry after failure is a new command.
        dedupe_key=f"{Topic.SEND_PAYOUT.value}:{payout.request_id}",
        now=clock.now(),
    )


def release_due_payouts(session: Session, clock: Clock, *, limit: int = 500) -> int:
    """Release every scheduled payout whose hold has ended. Returns how many were released.

    A booking with a refund still in flight is skipped until the refund settles, so we never
    pay a host money that is on its way back to the guest.
    """
    refund_in_flight = exists().where(
        Refund.booking_id == Booking.id, Refund.status == RefundStatus.PENDING
    )
    rows = session.execute(
        select(Payout, Booking)
        .join(Booking, Booking.id == Payout.booking_id)
        .where(
            Payout.status == PayoutStatus.SCHEDULED,
            Payout.release_at <= clock.now(),
            Booking.status.in_(RELEASABLE_STATES),
            ~refund_in_flight,
        )
        .order_by(Payout.release_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for payout, booking in rows:
        transition(session, booking, BookingStatus.RELEASED, "hold period ended")
        payout.status = PayoutStatus.QUEUED
        queue_send(session, payout, clock)
    return len(rows)


def complete_payout(session: Session, payout: Payout, booking: Booking) -> None:
    """The provider confirmed the host was paid: record it in the ledger and close the booking."""
    Ledger(session).post(
        event_type="payout.sent",
        idempotency_key=f"payout:{payout.id}",
        lines=payout_lines(payout.amount, str(booking.host_id)),
        booking_id=booking.id,
    )
    payout.status = PayoutStatus.PAID
    payout.failure_reason = None
    transition(session, booking, BookingStatus.PAID_OUT, "host paid")


def fail_payout(session: Session, payout: Payout, booking: Booking, reason: str) -> None:
    """The provider refused the payout. Nothing left our account; the host is still owed."""
    payout.status = PayoutStatus.FAILED
    payout.failure_reason = reason
    payout.send_attempt += 1
    transition(session, booking, BookingStatus.PAYOUT_FAILED, f"payout failed: {reason}")
    logger.warning("Payout %s failed: %s", payout.id, reason)


def retry_failed_payout(session: Session, clock: Clock, booking: Booking) -> Payout:
    """Re-queue a payout that failed definitively (e.g. after the host fixed their details)."""
    payout = payout_for(session, booking)
    if payout is None or booking.status is not BookingStatus.PAYOUT_FAILED:
        raise InvalidTransition("Only a failed payout can be retried")
    transition(session, booking, BookingStatus.RELEASED, "payout retry requested")
    payout.status = PayoutStatus.QUEUED
    queue_send(session, payout, clock)
    return payout


def block_payout(payout: Payout, reason: str) -> None:
    if payout.status in {PayoutStatus.SCHEDULED, PayoutStatus.QUEUED}:
        payout.status = PayoutStatus.BLOCKED
        payout.review_reason = reason


def approve_blocked_payout(
    session: Session, clock: Clock, booking: Booking, reviewer_id: uuid.UUID
) -> Payout:
    """An operator cleared a held payout; it goes back on its normal release schedule."""
    payout = payout_for(session, booking)
    if payout is None or payout.status is not PayoutStatus.BLOCKED:
        raise InvalidTransition("This payout is not blocked")
    payout.status = PayoutStatus.SCHEDULED
    payout.review_reason = None
    assessment = session.scalar(
        select(RiskAssessment).where(RiskAssessment.booking_id == booking.id)
    )
    if assessment is not None and assessment.reviewed_at is None:
        assessment.reviewed_at = clock.now()
        assessment.reviewed_by = reviewer_id
    return payout
