"""Disputes: a guest claims a refund while funds are held; the host responds; AI recommends;
an operator decides."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.ai.mediator import DisputeCase, EvidenceItem
from settle.core.errors import InvalidTransition, NotFound, ValidationFailed
from settle.db.models import (
    Booking,
    Dispute,
    DisputeEvidence,
    DisputeParty,
    DisputeStatus,
    EvidenceKind,
    RefundSource,
    User,
)
from settle.domain.money import Money
from settle.domain.states import BookingStatus
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic
from settle.services.context import Services
from settle.services.lifecycle import transition
from settle.services.refunds import captured_intent, create_refund, pending_refund_total


def get_dispute(session: Session, dispute_id: uuid.UUID) -> Dispute:
    dispute = session.get(Dispute, dispute_id)
    if dispute is None:
        raise NotFound(f"Dispute {dispute_id} not found")
    return dispute


def list_disputes(session: Session, *, status: DisputeStatus | None = None) -> Sequence[Dispute]:
    query = select(Dispute).order_by(Dispute.created_at.desc()).limit(200)
    if status is not None:
        query = query.where(Dispute.status == status)
    return session.scalars(query).all()


def max_refundable(session: Session, booking: Booking) -> Money:
    intent = captured_intent(session, booking)
    remaining = intent.remaining_split.gross
    return remaining - Money(pending_refund_total(session, booking.id), remaining.currency)


def open_dispute(
    session: Session,
    *,
    booking: Booking,
    reason: str,
    claim: Money,
    statement: str | None,
) -> Dispute:
    """Only while funds are held: once the host is paid, the money has left the platform."""
    if booking.status is not BookingStatus.HELD:
        raise InvalidTransition("A dispute can only be opened while the payment is held")
    if booking.cancelled_at is not None:
        raise InvalidTransition("This booking was cancelled; its refund followed the policy")
    if claim.currency != booking.currency:
        raise ValidationFailed(f"Claim must be in {booking.currency}")
    if not claim.is_positive or claim > max_refundable(session, booking):
        raise ValidationFailed(f"Claim must be between 0 and {max_refundable(session, booking)}")

    dispute = Dispute(
        booking_id=booking.id,
        reason=reason,
        claim_minor=claim.amount,
        currency=claim.currency,
        status=DisputeStatus.OPEN,
    )
    session.add(dispute)
    session.flush()
    if statement:
        add_evidence(
            session,
            dispute=dispute,
            party=DisputeParty.GUEST,
            kind=EvidenceKind.STATEMENT,
            content=statement,
            url=None,
        )
    transition(session, booking, BookingStatus.DISPUTED, f"dispute opened: {reason}"[:300])
    return dispute


def add_evidence(
    session: Session,
    *,
    dispute: Dispute,
    party: DisputeParty,
    kind: EvidenceKind,
    content: str,
    url: str | None,
) -> DisputeEvidence:
    if dispute.status is DisputeStatus.RESOLVED:
        raise InvalidTransition("This dispute has been decided")
    if kind is EvidenceKind.PHOTO and not url:
        raise ValidationFailed("A photo needs a URL")
    evidence = DisputeEvidence(
        dispute_id=dispute.id, party=party, kind=kind, content=content, url=url
    )
    session.add(evidence)
    session.flush()
    return evidence


def request_recommendation(session: Session, services: Services, dispute: Dispute) -> None:
    """Ask the mediator in the background (model calls never block a request)."""
    if dispute.status is DisputeStatus.RESOLVED:
        raise InvalidTransition("This dispute has been decided")
    evidence_count = len(dispute.evidence)
    enqueue(
        session,
        topic=Topic.RECOMMEND_DISPUTE,
        aggregate_id=str(dispute.booking_id),
        payload={"dispute_id": str(dispute.id)},
        # A new recommendation is worth having whenever new evidence has arrived.
        dedupe_key=f"{Topic.RECOMMEND_DISPUTE.value}:{dispute.id}:{evidence_count}",
        now=services.clock.now(),
    )


def record_recommendation(session: Session, dispute_id: uuid.UUID, services: Services) -> None:
    dispute = get_dispute(session, dispute_id)
    if dispute.status is DisputeStatus.RESOLVED:
        return
    booking = session.get_one(Booking, dispute.booking_id)
    case = DisputeCase(
        reason=dispute.reason,
        claim=Money(dispute.claim_minor, dispute.currency),
        max_refund=max_refundable(session, booking),
        nights=booking.nights,
        total=booking.total,
        evidence=tuple(
            EvidenceItem(
                id=str(item.id),
                party=item.party.value,
                kind=item.kind.value,
                content=item.content,
                url=item.url,
            )
            for item in dispute.evidence
        ),
    )
    result = services.mediator.recommend(case)
    dispute.recommendation = result.recommendation.model_dump()
    dispute.recommended_by = result.recommended_by
    dispute.status = DisputeStatus.RECOMMENDED


def decide_dispute(
    session: Session,
    services: Services,
    *,
    dispute: Dispute,
    decided_by: User,
    refund: Money,
    note: str,
) -> Dispute:
    """An operator's final decision. Zero means the host keeps everything."""
    if dispute.status is DisputeStatus.RESOLVED:
        raise InvalidTransition("This dispute has already been decided")
    booking = session.get_one(Booking, dispute.booking_id)
    if booking.status is not BookingStatus.DISPUTED:
        raise InvalidTransition(f"Booking is {booking.status}, not disputed")
    if refund.currency != dispute.currency or refund.amount < 0:
        raise ValidationFailed("Refund must be a non-negative amount in the dispute currency")
    if refund > max_refundable(session, booking):
        raise ValidationFailed(f"Refund cannot exceed {max_refundable(session, booking)}")

    dispute.status = DisputeStatus.RESOLVED
    dispute.decided_by = decided_by.id
    dispute.decided_at = services.clock.now()
    dispute.decision_refund_minor = refund.amount
    dispute.decision_note = note
    if refund.is_zero:
        transition(session, booking, BookingStatus.HELD, "dispute resolved for the host")
    else:
        create_refund(
            session,
            services.clock,
            booking=booking,
            amount=refund,
            source=RefundSource.DISPUTE,
            reason=f"dispute resolved: {note}"[:300] if note else "dispute resolved",
            dispute_id=dispute.id,
        )
    return dispute
