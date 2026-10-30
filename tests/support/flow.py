"""Helpers that drive a booking through its lifecycle at the service level."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.db.models import Booking, Payout
from settle.db.session import transaction
from settle.domain.policies import RefundQuote
from settle.domain.states import BookingStatus
from settle.ledger.accounts import AccountRef
from settle.ledger.posting import Ledger
from settle.providers.base import EventKind
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from settle.services.bookings import create_booking, get_booking
from settle.services.payments import PaymentService
from settle.services.payouts import payout_for, release_due_payouts
from settle.services.webhooks import WebhookReceipt, receive_webhook
from tests.support.data import CHECK_IN, CHECK_OUT


def book(
    container: Container,
    demo: SeedResult,
    *,
    listing_index: int = 0,
    check_in: date = CHECK_IN,
    check_out: date = CHECK_OUT,
) -> uuid.UUID:
    with transaction(container.session_factory) as session:
        booking = create_booking(
            session,
            guest_id=demo.guest.id,
            listing_id=demo.listings[listing_index].id,
            check_in=check_in,
            check_out=check_out,
            today=container.clock.now().date(),
        )
        return booking.id


def checkout_and_approve(container: Container, booking_id: uuid.UUID) -> None:
    with transaction(container.session_factory) as session:
        booking = get_booking(session, booking_id)
        service = PaymentService(session, container.services)
        service.start_checkout(booking)
        service.confirm_approval(booking)


def book_and_pay(
    container: Container,
    demo: SeedResult,
    *,
    listing_index: int = 0,
    check_in: date = CHECK_IN,
    check_out: date = CHECK_OUT,
) -> uuid.UUID:
    """Book, pay and run the worker until the funds are held."""
    booking_id = book(
        container, demo, listing_index=listing_index, check_in=check_in, check_out=check_out
    )
    checkout_and_approve(container, booking_id)
    container.relay.drain()
    return booking_id


def cancel(
    container: Container, booking_id: uuid.UUID, reason: str = "plans changed"
) -> RefundQuote:
    with transaction(container.session_factory) as session:
        booking = get_booking(session, booking_id)
        return PaymentService(session, container.services).cancel(booking, reason)


def release_payouts_after_check_in(container: Container, clock: FrozenClock) -> int:
    with transaction(container.session_factory) as session:
        release_at = max(session.scalars(select(Payout.release_at)))
    clock.set(release_at + timedelta(minutes=1))
    with transaction(container.session_factory) as session:
        return release_due_payouts(session, clock)


def payout_of(container: Container, booking_id: uuid.UUID) -> Payout:
    with transaction(container.session_factory) as session:
        payout = payout_for(session, get_booking(session, booking_id))
        assert payout is not None
        session.expunge(payout)
        return payout


def status_of(container: Container, booking_id: uuid.UUID) -> BookingStatus:
    with transaction(container.session_factory) as session:
        return session.get_one(Booking, booking_id).status


def balance(container: Container, ref: AccountRef) -> int:
    with transaction(container.session_factory) as session:
        return Ledger(session).balance(ref).amount


def host_owed(container: Container, demo: SeedResult) -> int:
    return balance(container, AccountRef.host_payable(str(demo.host.id), "USD"))


def ledger_is_balanced(container: Container) -> bool:
    with transaction(container.session_factory) as session:
        return not Ledger(session).unbalanced_entry_ids()


def deliver_webhook(
    container: Container, provider: FakeProvider, kind: EventKind, **data: str
) -> WebhookReceipt:
    body, headers = provider.build_webhook(kind, **data)
    with transaction(container.session_factory) as session:
        return receive_webhook(session, container.services, headers=headers, body=body)
