"""Booking and payment endpoints for guests (and read access for their hosts)."""

from __future__ import annotations

import uuid
from http import HTTPStatus

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from settle.api.deps import ContainerDep, CurrentUser, GuestUser, IdempotencyKey, SessionDep
from settle.api.idempotency import run_idempotent
from settle.api.schemas import (
    BookingOut,
    BookingSummaryOut,
    CancelBookingIn,
    CancellationQuoteOut,
    CheckoutOut,
    CreateBookingIn,
    MoneyOut,
    ProblemOut,
)
from settle.api.serializers import booking_out, booking_summary_out
from settle.domain.money import BPS_DENOMINATOR
from settle.domain.states import BookingStatus
from settle.services.access import ensure_can_view, ensure_guest_of
from settle.services.bookings import create_booking, get_booking, list_bookings
from settle.services.payments import PaymentService

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])

ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemOut} for code in (401, 403, 404, 409, 422)
}


@router.get("", response_model=list[BookingSummaryOut])
def list_mine(
    session: SessionDep, user: CurrentUser, status: BookingStatus | None = None
) -> list[BookingSummaryOut]:
    """Guests see their trips, hosts their reservations, operators everything."""
    return [booking_summary_out(b) for b in list_bookings(session, user, status=status)]


@router.post("", status_code=201, response_model=BookingOut, responses=ERRORS)
def create(
    body: CreateBookingIn, container: ContainerDep, user: GuestUser, key: IdempotencyKey
) -> JSONResponse:
    """Create a pending booking. The price comes from the listing, never from the client."""

    def operation(session: Session) -> BaseModel:
        booking = create_booking(
            session,
            guest_id=user.id,
            listing_id=body.listing_id,
            check_in=body.check_in,
            check_out=body.check_out,
            today=container.clock.now().date(),
        )
        return booking_out(session, booking)

    return run_idempotent(
        container,
        user=user,
        operation_name="create-booking",
        key=key,
        request_body=body.model_dump(mode="json"),
        status_code=HTTPStatus.CREATED,
        operation=operation,
    )


@router.get("/{booking_id}", response_model=BookingOut, responses=ERRORS)
def read(booking_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> BookingOut:
    booking = get_booking(session, booking_id)
    ensure_can_view(user, booking)
    return booking_out(session, booking)


@router.post("/{booking_id}/checkout", response_model=CheckoutOut, responses=ERRORS)
def checkout(
    booking_id: uuid.UUID, container: ContainerDep, user: GuestUser, key: IdempotencyKey
) -> JSONResponse:
    """Create the provider order and return the link where the guest approves the payment."""

    def operation(session: Session) -> BaseModel:
        booking = get_booking(session, booking_id)
        ensure_guest_of(user, booking)
        intent = PaymentService(session, container.services).start_checkout(booking)
        return CheckoutOut(
            booking_id=booking.id,
            provider_order_id=intent.provider_order_id,
            approval_url=intent.approval_url,
        )

    return run_idempotent(
        container,
        user=user,
        operation_name=f"checkout:{booking_id}",
        key=key,
        request_body={},
        status_code=HTTPStatus.OK,
        operation=operation,
    )


@router.post("/{booking_id}/confirm", status_code=202, response_model=BookingOut, responses=ERRORS)
def confirm(
    booking_id: uuid.UUID, container: ContainerDep, user: GuestUser, key: IdempotencyKey
) -> JSONResponse:
    """Called when the guest returns from approving at the provider. Authorization and capture
    then run in the background. The provider's webhook triggers the same step."""

    def operation(session: Session) -> BaseModel:
        booking = get_booking(session, booking_id)
        ensure_guest_of(user, booking)
        PaymentService(session, container.services).confirm_approval(booking)
        return booking_out(session, booking)

    return run_idempotent(
        container,
        user=user,
        operation_name=f"confirm:{booking_id}",
        key=key,
        request_body={},
        status_code=HTTPStatus.ACCEPTED,
        operation=operation,
    )


@router.get(
    "/{booking_id}/cancellation-quote", response_model=CancellationQuoteOut, responses=ERRORS
)
def cancellation_quote(
    booking_id: uuid.UUID, session: SessionDep, container: ContainerDep, user: CurrentUser
) -> CancellationQuoteOut:
    """What the guest would get back if they cancelled right now."""
    booking = get_booking(session, booking_id)
    ensure_can_view(user, booking)
    quote = PaymentService(session, container.services).quote_cancellation(booking)
    return CancellationQuoteOut(
        policy=quote.policy,
        refund=MoneyOut.of(quote.refund),
        refund_percent=quote.refund_bps * 100 // BPS_DENOMINATOR,
        explanation=quote.explanation,
    )


@router.post("/{booking_id}/cancel", status_code=202, response_model=BookingOut, responses=ERRORS)
def cancel(
    booking_id: uuid.UUID,
    body: CancelBookingIn,
    container: ContainerDep,
    user: GuestUser,
    key: IdempotencyKey,
) -> JSONResponse:
    """Cancel under the listing's policy: void if not charged, otherwise refund what it allows."""

    def operation(session: Session) -> BaseModel:
        booking = get_booking(session, booking_id)
        ensure_guest_of(user, booking)
        PaymentService(session, container.services).cancel(booking, body.reason)
        return booking_out(session, booking)

    return run_idempotent(
        container,
        user=user,
        operation_name=f"cancel:{booking_id}",
        key=key,
        request_body=body.model_dump(mode="json"),
        status_code=HTTPStatus.ACCEPTED,
        operation=operation,
    )
