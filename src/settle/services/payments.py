"""Guest-facing payment commands: checkout, approval and cancellation.

Only ``start_checkout`` calls the provider directly, because the guest needs the approval link
immediately and creating an order moves no money. Every money-moving call goes through the
outbox and runs in the worker.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.errors import InvalidTransition, ValidationFailed
from settle.db.models import Booking, Listing, PaymentIntent, RefundSource
from settle.domain.policies import RefundQuote, quote_refund
from settle.domain.states import BookingStatus
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic
from settle.services.context import Services
from settle.services.lifecycle import transition
from settle.services.refunds import create_refund, pending_refund_total
from settle.services.risk import assess_booking


def payment_intent_for(session: Session, booking: Booking) -> PaymentIntent | None:
    return session.scalar(select(PaymentIntent).where(PaymentIntent.booking_id == booking.id))


class PaymentService:
    def __init__(self, session: Session, services: Services) -> None:
        self._session = session
        self._services = services

    def start_checkout(self, booking: Booking) -> PaymentIntent:
        """Create (or return the existing) provider order for a pending booking."""
        existing = payment_intent_for(self._session, booking)
        if existing is not None:
            return existing
        if booking.status is not BookingStatus.PENDING:
            raise InvalidTransition(f"Cannot check out a booking that is {booking.status}")

        base = self._services.settings.public_base_url.rstrip("/")
        provider = self._services.provider
        order = provider.create_order(
            request_id=f"{booking.id}:create-order",
            amount=booking.total,
            reference=str(booking.id),
            return_url=f"{base}/trips/{booking.id}/return",
            cancel_url=f"{base}/trips/{booking.id}",
        )
        intent = PaymentIntent(
            booking_id=booking.id,
            provider=provider.name,
            amount_minor=booking.total_minor,
            currency=booking.currency,
            provider_order_id=order.order_id,
            approval_url=order.approval_url,
        )
        self._session.add(intent)
        self._session.flush()
        return intent

    def confirm_approval(self, booking: Booking) -> None:
        """The guest approved the order at the provider: score the risk, queue authorization.

        Reached from the guest's browser returning from the provider, or from the provider's
        own webhook; whichever comes first wins and the other is a no-op.
        """
        intent = payment_intent_for(self._session, booking)
        if intent is None:
            raise ValidationFailed("Start checkout before confirming approval")
        if booking.status is not BookingStatus.PENDING:
            return
        assess_booking(self._session, booking, self._services.risk, self._services.clock)
        enqueue(
            self._session,
            topic=Topic.AUTHORIZE_PAYMENT,
            aggregate_id=str(booking.id),
            payload={"booking_id": str(booking.id)},
            dedupe_key=f"authorize:{booking.id}",
            now=self._services.clock.now(),
        )

    def quote_cancellation(self, booking: Booking) -> RefundQuote:
        """What the guest would get back if they cancelled now."""
        listing = self._session.get_one(Listing, booking.listing_id)
        paid = booking.total
        if booking.status in {BookingStatus.PENDING, BookingStatus.AUTHORIZED}:
            # Nothing captured yet: cancelling releases the hold in full.
            return RefundQuote(listing.cancellation_policy, 10_000, paid, "Nothing charged yet")
        return quote_refund(
            listing.cancellation_policy,
            paid,
            cancelled_at=self._services.clock.now(),
            check_in_at=booking.check_in_at,
        )

    def cancel(self, booking: Booking, reason: str) -> RefundQuote:
        """Cancel: void if not charged yet; otherwise refund what the policy allows.

        If the policy leaves the host part of the money, the booking stays held and the host is
        paid that part after the would-be check-in, like any other payout.
        """
        if booking.cancelled_at is not None:
            raise InvalidTransition("This booking is already cancelled")
        quote = self.quote_cancellation(booking)
        now = self._services.clock.now()
        match booking.status:
            case BookingStatus.PENDING:
                transition(self._session, booking, BookingStatus.VOIDED, reason)
            case BookingStatus.AUTHORIZED:
                self._enqueue_void(booking, reason)
            case BookingStatus.CAPTURED | BookingStatus.HELD:
                self._refund_per_policy(booking, quote, reason)
            case _:
                raise InvalidTransition(f"A booking that is {booking.status} cannot be cancelled")
        booking.cancelled_at = now
        booking.cancellation_reason = reason
        return quote

    def _refund_per_policy(self, booking: Booking, quote: RefundQuote, reason: str) -> None:
        if pending_refund_total(self._session, booking.id):
            raise InvalidTransition("A refund for this booking is already in progress")
        if quote.refund.is_positive:
            create_refund(
                self._session,
                self._services.clock,
                booking=booking,
                amount=min(quote.refund, booking.total),
                source=RefundSource.CANCELLATION,
                reason=f"{reason} ({quote.explanation})",
            )

    def _enqueue_void(self, booking: Booking, reason: str) -> None:
        enqueue(
            self._session,
            topic=Topic.VOID_PAYMENT,
            aggregate_id=str(booking.id),
            payload={"booking_id": str(booking.id), "reason": reason},
            dedupe_key=f"{Topic.VOID_PAYMENT.value}:{booking.id}",
            now=self._services.clock.now(),
        )
