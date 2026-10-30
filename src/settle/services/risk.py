"""Scores a booking for fraud risk when the guest approves payment."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from settle.ai.risk import RiskAgent, RiskInput
from settle.core.clock import Clock
from settle.db.models import (
    Booking,
    Dispute,
    PaymentIntent,
    PaymentIntentStatus,
    RiskAction,
    RiskAssessment,
    User,
)


def assess_booking(
    session: Session, booking: Booking, agent: RiskAgent, clock: Clock
) -> RiskAssessment:
    """Score once per booking; later calls return the stored assessment."""
    existing = session.scalar(select(RiskAssessment).where(RiskAssessment.booking_id == booking.id))
    if existing is not None:
        return existing

    now = clock.now()
    guest = session.get_one(User, booking.guest_id)
    since = now - timedelta(hours=24)
    recent_bookings = session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.guest_id == guest.id, Booking.created_at >= since)
    )
    disputes = session.scalar(
        select(func.count())
        .select_from(Dispute)
        .join(Booking, Booking.id == Dispute.booking_id)
        .where(Booking.guest_id == guest.id)
    )
    declined = session.scalar(
        select(func.count())
        .select_from(PaymentIntent)
        .join(Booking, Booking.id == PaymentIntent.booking_id)
        .where(Booking.guest_id == guest.id, PaymentIntent.status == PaymentIntentStatus.FAILED)
    )
    result = agent.assess(
        RiskInput(
            total=booking.total,
            nights=booking.nights,
            lead_time=booking.check_in_at - now,
            guest_account_age=now - guest.created_at,
            guest_bookings_last_24h=recent_bookings or 0,
            guest_disputes_opened=disputes or 0,
            guest_declined_payments=declined or 0,
        )
    )
    assessment = RiskAssessment(
        booking_id=booking.id,
        score=result.score,
        action=RiskAction(result.decision.value),
        signals=[asdict(signal) for signal in result.signals],
        explanation=result.explanation,
        explained_by=result.explained_by,
        created_at=now,
    )
    session.add(assessment)
    session.flush()
    return assessment
