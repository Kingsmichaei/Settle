"""Chaos tests: timeouts, lost responses, duplicate and late events, crashes, races."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.db.models import Booking, JournalEntry, OutboxEvent, OutboxStatus
from settle.db.session import transaction
from settle.domain.states import BookingStatus as S
from settle.ledger.accounts import AccountRef
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from tests.support.flow import (
    balance,
    book,
    book_and_pay,
    checkout_and_approve,
    ledger_is_balanced,
    release_payouts_after_check_in,
    status_of,
)

# Longer than any backoff the relay will pick with the test retry policy.
RETRY_WINDOW = timedelta(minutes=10)


def journal_entries(container: Container) -> int:
    with transaction(container.session_factory) as session:
        return session.scalar(select(func.count()).select_from(JournalEntry)) or 0


def test_capture_timeout_after_success_charges_exactly_once(
    container: Container, demo: SeedResult, provider: FakeProvider, clock: FrozenClock
) -> None:
    # The provider captures the money, but the response is lost (a timeout).
    provider.fail_next_transient("capture", lose_response=True)
    booking_id = book(container, demo)
    checkout_and_approve(container, booking_id)

    container.relay.run_once()  # authorize
    container.relay.run_once()  # capture: money moves, then the response is lost
    assert status_of(container, booking_id) is S.AUTHORIZED

    clock.advance(RETRY_WINDOW)
    container.relay.drain()  # retry with the same request id

    assert status_of(container, booking_id) is S.HELD
    assert provider.executions["capture"] == 1
    assert journal_entries(container) == 1
    assert balance(container, AccountRef.provider_cash("USD")) == 50_000


def test_payout_timeout_after_success_pays_exactly_once(
    container: Container, demo: SeedResult, provider: FakeProvider, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    provider.fail_next_transient("payout", lose_response=True)
    release_payouts_after_check_in(container, clock)
    container.relay.run_once()
    assert status_of(container, booking_id) is S.RELEASED

    clock.advance(RETRY_WINDOW)
    container.relay.drain()

    assert status_of(container, booking_id) is S.PAID_OUT
    assert provider.executions["payout"] == 1
    assert balance(container, AccountRef.host_payable(str(demo.host.id), "USD")) == 0


def test_replayed_event_after_success_is_a_no_op(
    container: Container, demo: SeedResult, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    entries_before = journal_entries(container)

    # Simulate a duplicate delivery: put the capture command back in the queue.
    with transaction(container.session_factory) as session:
        event = session.scalars(
            select(OutboxEvent).where(OutboxEvent.topic == Topic.CAPTURE_PAYMENT)
        ).one()
        event.status = OutboxStatus.PENDING
    container.relay.drain()

    assert status_of(container, booking_id) is S.HELD
    assert provider.executions["capture"] == 1
    assert journal_entries(container) == entries_before


def test_enqueuing_the_same_command_twice_creates_one_event(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book(container, demo)
    with transaction(container.session_factory) as session:
        for _ in range(3):
            enqueue(
                session,
                topic=Topic.AUTHORIZE_PAYMENT,
                aggregate_id=str(booking_id),
                payload={"booking_id": str(booking_id)},
                dedupe_key=f"authorize:{booking_id}",
                now=clock.now(),
            )
        count = session.scalar(select(func.count()).select_from(OutboxEvent))
    assert count == 1


def test_repeated_transient_failures_end_in_a_dead_event(
    container: Container, demo: SeedResult, provider: FakeProvider, clock: FrozenClock
) -> None:
    for _ in range(container.settings.outbox_max_attempts):
        provider.fail_next_transient("authorize")
    booking_id = book(container, demo)
    checkout_and_approve(container, booking_id)

    for _ in range(container.settings.outbox_max_attempts):
        container.relay.run_once()
        clock.advance(RETRY_WINDOW)

    with transaction(container.session_factory) as session:
        event = session.scalars(select(OutboxEvent)).one()
        assert event.status is OutboxStatus.DEAD
        assert event.last_error is not None
        assert "simulated timeout" in event.last_error
    assert status_of(container, booking_id) is S.PENDING  # nothing moved
    assert provider.executions["authorize"] == 0


def test_cancel_racing_capture_ends_in_a_refund(
    container: Container, demo: SeedResult, provider: FakeProvider, clock: FrozenClock
) -> None:
    """Guest cancels while authorized, but the capture runs first. Void must turn into refund."""
    booking_id = book(container, demo)
    checkout_and_approve(container, booking_id)
    container.relay.run_once()  # authorize; capture is now queued
    with transaction(container.session_factory) as session:
        enqueue(
            session,
            topic=Topic.VOID_PAYMENT,
            aggregate_id=str(booking_id),
            payload={"booking_id": str(booking_id), "reason": "guest cancelled"},
            dedupe_key=f"{Topic.VOID_PAYMENT.value}:{booking_id}",
            now=clock.now() + RETRY_WINDOW,  # runs after the capture
        )
    container.relay.run_once()  # capture
    clock.advance(RETRY_WINDOW * 2)
    container.relay.drain()  # void sees a captured booking and queues a refund

    assert status_of(container, booking_id) is S.REFUNDED
    assert provider.executions["void"] == 0
    assert provider.executions["refund"] == 1
    assert balance(container, AccountRef.provider_cash("USD")) == 0
    assert ledger_is_balanced(container)


def test_concurrent_updates_to_one_booking_are_detected(
    container: Container, demo: SeedResult
) -> None:
    booking_id = book(container, demo)
    first, second = container.session_factory(), container.session_factory()
    try:
        a = first.get_one(Booking, booking_id)
        b = second.get_one(Booking, booking_id)
        a.status = S.VOIDED
        first.commit()
        b.status = S.AUTHORIZED
        with pytest.raises(StaleDataError):
            second.commit()
    finally:
        first.close()
        second.close()
    assert status_of(container, booking_id) is S.VOIDED
