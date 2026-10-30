"""Outbox handlers: the code that actually moves money (and runs AI), executed by the worker.

Rules every handler follows:

* Re-read the current state and do nothing if it has already moved past this step, so
  duplicate or late events are harmless.
* Use a deterministic provider ``request_id``, so a retry after a timeout can't double-move.
* A ``TransientProviderError`` propagates: the relay rolls back and retries later.
* A ``PermanentProviderError`` is handled here, by moving the booking to the right state.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.errors import ValidationFailed
from settle.db.models import (
    Booking,
    OutboxEvent,
    PaymentIntent,
    PaymentIntentStatus,
    Payout,
    PayoutStatus,
    Refund,
    RefundSource,
    RefundStatus,
    RiskAction,
    RiskAssessment,
    User,
    WebhookEvent,
)
from settle.domain.fees import RefundSplit, split_payment
from settle.domain.fx import FxQuote, convert
from settle.domain.states import FUNDS_HELD_STATES, BookingStatus
from settle.ledger.posting import Ledger
from settle.ledger.recipes import capture_lines, refund_lines
from settle.outbox.publisher import enqueue
from settle.outbox.relay import Handler
from settle.outbox.topics import Topic
from settle.providers.base import EventKind, PayoutState, PermanentProviderError
from settle.services.context import Services
from settle.services.disputes import record_recommendation
from settle.services.lifecycle import transition
from settle.services.payments import PaymentService
from settle.services.payouts import block_payout, complete_payout, fail_payout, payout_for
from settle.services.reconciliation import run_reconciliation
from settle.services.refunds import create_refund

logger = logging.getLogger(__name__)


class RetryLater(Exception):
    """The event arrived before the state it depends on; the relay will retry it."""


class PaymentHandlers:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._provider = services.provider
        self._clock = services.clock

    def as_mapping(self) -> Mapping[Topic, Handler]:
        return {
            Topic.AUTHORIZE_PAYMENT: self.authorize,
            Topic.CAPTURE_PAYMENT: self.capture,
            Topic.VOID_PAYMENT: self.void,
            Topic.REFUND_PAYMENT: self.refund,
            Topic.SEND_PAYOUT: self.send_payout,
            Topic.PROCESS_WEBHOOK: self.process_webhook,
            Topic.RECOMMEND_DISPUTE: self.recommend_dispute,
            Topic.RUN_RECONCILIATION: self.reconcile,
        }

    # ---- guest charge -------------------------------------------------------------------

    def authorize(self, session: Session, event: OutboxEvent) -> None:
        booking, intent = self._booking_and_intent(session, event)
        if booking.status is not BookingStatus.PENDING:
            return
        try:
            authorization_id = self._provider.authorize_order(
                request_id=f"{intent.id}:authorize", order_id=intent.provider_order_id
            )
        except PermanentProviderError as exc:
            intent.status = PaymentIntentStatus.FAILED
            intent.failure_reason = str(exc)
            transition(session, booking, BookingStatus.VOIDED, f"authorization declined: {exc}")
            return

        intent.provider_authorization_id = authorization_id
        intent.status = PaymentIntentStatus.AUTHORIZED
        transition(session, booking, BookingStatus.AUTHORIZED, "funds authorized")
        self._enqueue(session, Topic.CAPTURE_PAYMENT, booking)

    def capture(self, session: Session, event: OutboxEvent) -> None:
        booking, intent = self._booking_and_intent(session, event)
        if booking.status is not BookingStatus.AUTHORIZED:
            return
        if intent.provider_authorization_id is None:
            raise RuntimeError(f"Payment intent {intent.id} is authorized without an id")
        try:
            capture_id = self._provider.capture_authorization(
                request_id=f"{intent.id}:capture",
                authorization_id=intent.provider_authorization_id,
                amount=intent.amount,
            )
        except PermanentProviderError as exc:
            intent.failure_reason = str(exc)
            self._enqueue(session, Topic.VOID_PAYMENT, booking, reason=f"capture failed: {exc}")
            return

        split = split_payment(intent.amount, self._services.settings.platform_fee_bps)
        intent.provider_capture_id = capture_id
        intent.platform_fee_minor = split.platform_fee.amount
        intent.status = PaymentIntentStatus.CAPTURED
        Ledger(session).post(
            event_type="payment.captured",
            idempotency_key=f"capture:{intent.id}",
            lines=capture_lines(split, str(booking.host_id)),
            booking_id=booking.id,
        )
        transition(session, booking, BookingStatus.CAPTURED, "funds captured")
        payout = Payout(
            booking_id=booking.id,
            host_id=booking.host_id,
            amount_minor=split.host_amount.amount,
            currency=split.host_amount.currency,
            status=PayoutStatus.SCHEDULED,
            release_at=booking.check_in_at
            + timedelta(hours=self._services.settings.payout_hold_hours),
        )
        session.add(payout)
        risk = session.scalar(select(RiskAssessment).where(RiskAssessment.booking_id == booking.id))
        if risk is not None and risk.action is RiskAction.HOLD:
            block_payout(payout, f"risk score {risk.score}: {risk.explanation}"[:300])
        transition(session, booking, BookingStatus.HELD, "holding funds until after check-in")

    def void(self, session: Session, event: OutboxEvent) -> None:
        booking, intent = self._booking_and_intent(session, event)
        reason = str(event.payload.get("reason", "cancelled"))
        if booking.status in {BookingStatus.CAPTURED, BookingStatus.HELD}:
            # Capture won the race with the cancellation; the guest gets everything back.
            create_refund(
                session,
                self._clock,
                booking=booking,
                amount=intent.remaining_split.gross,
                source=RefundSource.CANCELLATION,
                reason=reason,
            )
            return
        if booking.status is not BookingStatus.AUTHORIZED:
            return
        if intent.provider_authorization_id is None:
            raise RuntimeError(f"Payment intent {intent.id} is authorized without an id")
        self._provider.void_authorization(
            request_id=f"{intent.id}:void", authorization_id=intent.provider_authorization_id
        )
        intent.status = PaymentIntentStatus.VOIDED
        transition(session, booking, BookingStatus.VOIDED, reason)

    def refund(self, session: Session, event: OutboxEvent) -> None:
        refund = session.get_one(Refund, uuid.UUID(str(event.payload["refund_id"])))
        if refund.status is not RefundStatus.PENDING:
            return
        booking = session.get_one(Booking, refund.booking_id)
        intent = self._intent(session, booking)
        if booking.status not in {BookingStatus.CAPTURED, *FUNDS_HELD_STATES}:
            refund.status = RefundStatus.FAILED
            refund.failure_reason = f"booking is {booking.status}; funds are no longer held"
            return
        if intent.provider_capture_id is None:
            raise RuntimeError(f"Payment intent {intent.id} has no capture to refund")

        try:
            refund.provider_refund_id = self._provider.refund_capture(
                request_id=f"{refund.id}:refund",
                capture_id=intent.provider_capture_id,
                amount=refund.amount,
            )
        except PermanentProviderError as exc:
            # Money is still with us. Block the payout so the host isn't paid for money the
            # guest is owed, and leave the booking for an operator to resolve.
            refund.status = RefundStatus.FAILED
            refund.failure_reason = str(exc)
            payout = payout_for(session, booking)
            if payout is not None:
                block_payout(payout, f"refund refused by provider: {exc}"[:300])
            logger.error("Refund %s for booking %s was refused: %s", refund.id, booking.id, exc)
            return

        split = RefundSplit(
            total=refund.amount, from_host=refund.from_host, from_fee=refund.from_fee
        )
        Ledger(session).post(
            event_type="payment.refunded",
            idempotency_key=f"refund:{refund.id}",
            lines=refund_lines(split, str(booking.host_id)),
            booking_id=booking.id,
        )
        refund.status = RefundStatus.SUCCEEDED
        intent.refunded_minor += refund.amount_minor
        intent.refunded_fee_minor += refund.from_fee_minor
        fully_refunded = intent.refunded_minor == intent.amount_minor
        intent.status = (
            PaymentIntentStatus.REFUNDED
            if fully_refunded
            else PaymentIntentStatus.PARTIALLY_REFUNDED
        )

        payout = payout_for(session, booking)
        if payout is not None:
            payout.amount_minor -= refund.from_host_minor
            if payout.amount_minor == 0:
                payout.status = PayoutStatus.CANCELLED
        if fully_refunded:
            transition(session, booking, BookingStatus.REFUNDED, refund.reason)
        elif booking.status is BookingStatus.CAPTURED:
            raise RuntimeError("A partial refund is only possible once funds are held")
        else:
            transition(session, booking, BookingStatus.PARTIALLY_REFUNDED, refund.reason)

    # ---- host payout --------------------------------------------------------------------

    def send_payout(self, session: Session, event: OutboxEvent) -> None:
        payout = session.get_one(Payout, uuid.UUID(str(event.payload["payout_id"])))
        booking = session.get_one(Booking, payout.booking_id)
        if payout.status is not PayoutStatus.QUEUED or booking.status is not BookingStatus.RELEASED:
            return

        host = session.get_one(User, booking.host_id)
        try:
            if not host.payout_email:
                raise PermanentProviderError("host has no payout method on file")
            quote = self._quote(payout, host.default_currency)
            payout.payout_currency = quote.target.currency
            payout.payout_amount_minor = quote.target.amount
            payout.fx_rate = str(quote.rate)
            submission = self._provider.send_payout(
                request_id=payout.request_id,
                payout_id=payout.id,
                recipient=host.payout_email,
                amount=quote.target,
            )
        except PermanentProviderError as exc:
            fail_payout(session, payout, booking, str(exc))
            return

        payout.provider_payout_id = submission.provider_payout_id
        payout.sent_at = self._clock.now()
        if submission.state is PayoutState.SUCCEEDED:
            complete_payout(session, payout, booking)
        else:
            payout.status = PayoutStatus.SENT  # the provider's webhook will confirm it

    def _quote(self, payout: Payout, target_currency: str) -> FxQuote:
        try:
            return convert(payout.amount, target_currency, self._services.fx)
        except ValidationFailed:
            # No rate configured: pay in the settlement currency and let the provider convert.
            logger.info(
                "No FX rate %s to %s; paying in %s",
                payout.currency,
                target_currency,
                payout.currency,
            )
            return convert(payout.amount, payout.currency, self._services.fx)

    # ---- webhooks -----------------------------------------------------------------------

    def process_webhook(self, session: Session, event: OutboxEvent) -> None:
        stored = session.get_one(WebhookEvent, uuid.UUID(str(event.payload["webhook_event_id"])))
        notice = self._provider.parse_webhook(json.dumps(stored.payload).encode())

        match notice.kind:
            case EventKind.ORDER_APPROVED:
                intent = session.scalar(
                    select(PaymentIntent).where(PaymentIntent.provider_order_id == notice.order_id)
                )
                if intent is not None:
                    booking = session.get_one(Booking, intent.booking_id)
                    PaymentService(session, self._services).confirm_approval(booking)
            case EventKind.PAYOUT_SUCCEEDED | EventKind.PAYOUT_FAILED:
                self._settle_payout(session, notice.payout_id, notice.kind, notice.reason)
            case EventKind.CAPTURE_REVERSED:
                intent = session.scalar(
                    select(PaymentIntent).where(
                        PaymentIntent.provider_capture_id == notice.capture_id
                    )
                )
                if intent is not None:
                    booking = session.get_one(Booking, intent.booking_id)
                    payout = payout_for(session, booking)
                    if payout is not None:
                        block_payout(payout, "chargeback: the guest's payment was reversed")
                    logger.critical("Chargeback on booking %s", booking.id)
            case EventKind.IGNORED:
                logger.debug("Ignoring %s webhook %s", notice.event_type, notice.event_id)

    def _settle_payout(
        self, session: Session, payout_id: uuid.UUID | None, kind: EventKind, reason: str | None
    ) -> None:
        payout = session.get(Payout, payout_id) if payout_id else None
        if payout is None:
            logger.warning("Payout webhook for unknown payout %s", payout_id)
            return
        if payout.status in {PayoutStatus.PAID, PayoutStatus.FAILED}:
            return  # already settled; a duplicate or late notice
        if payout.status is not PayoutStatus.SENT:
            raise RetryLater(f"Payout {payout.id} is {payout.status}; waiting for it to be sent")
        booking = session.get_one(Booking, payout.booking_id)
        if kind is EventKind.PAYOUT_SUCCEEDED:
            complete_payout(session, payout, booking)
        else:
            fail_payout(session, payout, booking, reason or "rejected by the provider")

    # ---- AI and reconciliation ----------------------------------------------------------

    def recommend_dispute(self, session: Session, event: OutboxEvent) -> None:
        record_recommendation(session, uuid.UUID(str(event.payload["dispute_id"])), self._services)

    def reconcile(self, session: Session, event: OutboxEvent) -> None:
        run_reconciliation(session, self._services)

    # ---- helpers ------------------------------------------------------------------------

    @staticmethod
    def _intent(session: Session, booking: Booking) -> PaymentIntent:
        return session.scalars(
            select(PaymentIntent).where(PaymentIntent.booking_id == booking.id)
        ).one()

    def _booking_and_intent(
        self, session: Session, event: OutboxEvent
    ) -> tuple[Booking, PaymentIntent]:
        booking = session.get_one(Booking, uuid.UUID(str(event.payload["booking_id"])))
        return booking, self._intent(session, booking)

    def _enqueue(
        self, session: Session, topic: Topic, booking: Booking, *, reason: str | None = None
    ) -> None:
        payload: dict[str, str] = {"booking_id": str(booking.id)}
        if reason is not None:
            payload["reason"] = reason
        enqueue(
            session,
            topic=topic,
            aggregate_id=str(booking.id),
            payload=payload,
            dedupe_key=f"{topic.value}:{booking.id}",
            now=self._clock.now(),
        )
