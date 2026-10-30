"""Turn ORM objects into response schemas."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.api.schemas import (
    BookingOut,
    BookingSummaryOut,
    DisputeOut,
    EvidenceOut,
    ListingOut,
    MoneyOut,
    PaymentOut,
    PayoutOut,
    RecommendationOut,
    RefundOut,
    RiskOut,
    StatusChangeOut,
)
from settle.core.errors import SettleError
from settle.db.models import (
    Booking,
    BookingStatusChange,
    Dispute,
    DisputeStatus,
    Listing,
    Payout,
    Refund,
    RiskAssessment,
    User,
)
from settle.domain.money import Money
from settle.domain.policies import POLICY_SUMMARIES
from settle.services.disputes import max_refundable
from settle.services.payments import payment_intent_for


def listing_out(session: Session, listing: Listing) -> ListingOut:
    host = session.get_one(User, listing.host_id)
    return ListingOut(
        id=listing.id,
        title=listing.title,
        city=listing.city,
        country=listing.country,
        image_url=listing.image_url,
        nightly_price=MoneyOut.of(listing.nightly_price),
        cancellation_policy=listing.cancellation_policy,
        policy_summary=POLICY_SUMMARIES[listing.cancellation_policy],
        host_name=host.full_name,
    )


def payout_out(payout: Payout) -> PayoutOut:
    received = payout.payout_amount
    return PayoutOut(
        status=payout.status,
        amount=MoneyOut.of(payout.amount),
        host_receives=MoneyOut.of(received) if received else None,
        fx_rate=payout.fx_rate,
        release_at=payout.release_at,
        provider_payout_id=payout.provider_payout_id,
        failure_reason=payout.failure_reason,
        review_reason=payout.review_reason,
    )


def booking_summary_out(booking: Booking) -> BookingSummaryOut:
    return BookingSummaryOut(
        id=booking.id,
        status=booking.status,
        listing_title=booking.listing.title,
        check_in=booking.check_in,
        check_out=booking.check_out,
        total=MoneyOut.of(booking.total),
        created_at=booking.created_at,
    )


def booking_out(session: Session, booking: Booking) -> BookingOut:
    session.flush()
    intent = payment_intent_for(session, booking)
    payout = session.scalar(select(Payout).where(Payout.booking_id == booking.id))
    risk = session.scalar(select(RiskAssessment).where(RiskAssessment.booking_id == booking.id))
    refunds = session.scalars(
        select(Refund).where(Refund.booking_id == booking.id).order_by(Refund.created_at)
    ).all()
    dispute_ids = session.scalars(
        select(Dispute.id).where(Dispute.booking_id == booking.id).order_by(Dispute.created_at)
    ).all()
    history = session.scalars(
        select(BookingStatusChange)
        .where(BookingStatusChange.booking_id == booking.id)
        .order_by(BookingStatusChange.position)
    ).all()
    return BookingOut(
        id=booking.id,
        status=booking.status,
        listing=listing_out(session, session.get_one(Listing, booking.listing_id)),
        guest_id=booking.guest_id,
        host_id=booking.host_id,
        check_in=booking.check_in,
        check_out=booking.check_out,
        nights=booking.nights,
        total=MoneyOut.of(booking.total),
        cancelled_at=booking.cancelled_at,
        payment=(
            PaymentOut(
                status=intent.status,
                amount=MoneyOut.of(intent.amount),
                refunded=MoneyOut.of(Money(intent.refunded_minor, intent.currency)),
                provider=intent.provider,
                provider_order_id=intent.provider_order_id,
                approval_url=intent.approval_url,
            )
            if intent
            else None
        ),
        payout=payout_out(payout) if payout else None,
        refunds=[
            RefundOut(
                id=refund.id,
                status=refund.status,
                source=refund.source,
                amount=MoneyOut.of(refund.amount),
                from_host=MoneyOut.of(refund.from_host),
                from_fee=MoneyOut.of(refund.from_fee),
                reason=refund.reason,
                created_at=refund.created_at,
            )
            for refund in refunds
        ],
        risk=RiskOut.model_validate(risk) if risk else None,
        dispute_ids=list(dispute_ids),
        history=[StatusChangeOut.model_validate(change) for change in history],
        created_at=booking.created_at,
    )


def dispute_out(session: Session, dispute: Dispute) -> DisputeOut:
    session.flush()
    booking = session.get_one(Booking, dispute.booking_id)
    recommendation = None
    if dispute.recommendation and dispute.recommended_by:
        data = dispute.recommendation
        recommendation = RecommendationOut(
            refund=MoneyOut.of(Money(int(data["refund_minor"]), dispute.currency)),
            reasoning=str(data["reasoning"]),
            cited_evidence_ids=[str(ref) for ref in data.get("cited_evidence_ids", [])],
            confidence=float(data["confidence"]),
            recommended_by=dispute.recommended_by,
        )
    refundable = None
    if dispute.status is not DisputeStatus.RESOLVED:
        try:
            refundable = MoneyOut.of(max_refundable(session, booking))
        except SettleError:
            refundable = None
    return DisputeOut(
        id=dispute.id,
        booking_id=dispute.booking_id,
        status=dispute.status,
        reason=dispute.reason,
        claim=MoneyOut.of(Money(dispute.claim_minor, dispute.currency)),
        max_refundable=refundable,
        evidence=[EvidenceOut.model_validate(item) for item in dispute.evidence],
        recommendation=recommendation,
        decision_refund=(
            MoneyOut.of(Money(dispute.decision_refund_minor, dispute.currency))
            if dispute.decision_refund_minor is not None
            else None
        ),
        decision_note=dispute.decision_note,
        decided_at=dispute.decided_at,
        created_at=dispute.created_at,
    )
