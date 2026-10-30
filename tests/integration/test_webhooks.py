"""Provider webhooks: signature checks, dedupe, async payouts, approvals and chargebacks."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.core.errors import Unauthenticated
from settle.db.models import PaymentIntent, PayoutStatus, WebhookEvent
from settle.db.session import transaction
from settle.domain.states import BookingStatus as S
from settle.providers.base import EventKind
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from settle.services.bookings import get_booking
from settle.services.payments import PaymentService
from settle.services.webhooks import receive_webhook
from tests.support.flow import (
    book,
    book_and_pay,
    deliver_webhook,
    host_owed,
    payout_of,
    release_payouts_after_check_in,
    status_of,
)


def test_unsigned_webhook_is_rejected(client: TestClient, provider: FakeProvider) -> None:
    body, _ = provider.build_webhook(EventKind.ORDER_APPROVED, order_id="ORDER-1")
    response = client.post("/v1/webhooks/fake", content=body, headers={"x-fake-signature": "bad"})
    assert response.status_code == 401


def test_tampered_webhook_is_rejected(container: Container, provider: FakeProvider) -> None:
    body, headers = provider.build_webhook(EventKind.ORDER_APPROVED, order_id="ORDER-1")
    with transaction(container.session_factory) as session, pytest.raises(Unauthenticated):
        receive_webhook(
            session, container.services, headers=headers, body=body.replace(b"ORDER-1", b"ORDER-2")
        )


def test_wrong_provider_path_is_not_found(client: TestClient) -> None:
    assert client.post("/v1/webhooks/paypal", content=b"{}").status_code == 404


def test_approval_webhook_starts_the_payment(
    client: TestClient, container: Container, demo: SeedResult, provider: FakeProvider
) -> None:
    booking_id = book(container, demo)
    with transaction(container.session_factory) as session:
        intent = PaymentService(session, container.services).start_checkout(
            get_booking(session, booking_id)
        )
        order_id = intent.provider_order_id

    body, headers = provider.build_webhook(EventKind.ORDER_APPROVED, order_id=order_id)
    response = client.post("/v1/webhooks/fake", content=body, headers=headers)
    assert response.json() == {"received": True, "duplicate": False}

    container.relay.drain()
    assert status_of(container, booking_id) is S.HELD


def test_duplicate_deliveries_are_counted_and_processed_once(
    client: TestClient, provider: FakeProvider, container: Container
) -> None:
    body, headers = provider.build_webhook(EventKind.IGNORED, event_id="EVT-1")
    first = client.post("/v1/webhooks/fake", content=body, headers=headers).json()
    second = client.post("/v1/webhooks/fake", content=body, headers=headers).json()
    assert (first["duplicate"], second["duplicate"]) == (False, True)
    with transaction(container.session_factory) as session:
        stored = session.scalars(select(WebhookEvent)).one()
        assert stored.deliveries == 2


@pytest.fixture
def async_provider(provider: FakeProvider) -> FakeProvider:
    provider.async_payouts = True
    return provider


def test_async_payout_completes_on_its_webhook(
    container: Container, demo: SeedResult, clock: FrozenClock, async_provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()

    payout = payout_of(container, booking_id)
    assert payout.status is PayoutStatus.SENT
    assert host_owed(container, demo) == 42_500  # not paid until the provider confirms

    deliver_webhook(container, async_provider, EventKind.PAYOUT_SUCCEEDED, payout_id=str(payout.id))
    container.relay.drain()
    assert status_of(container, booking_id) is S.PAID_OUT
    assert host_owed(container, demo) == 0


def test_async_payout_failure_marks_it_failed(
    container: Container, demo: SeedResult, clock: FrozenClock, async_provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    payout = payout_of(container, booking_id)

    deliver_webhook(
        container,
        async_provider,
        EventKind.PAYOUT_FAILED,
        payout_id=str(payout.id),
        reason="RECEIVER_UNREGISTERED",
    )
    container.relay.drain()
    assert status_of(container, booking_id) is S.PAYOUT_FAILED
    assert payout_of(container, booking_id).failure_reason == "RECEIVER_UNREGISTERED"
    assert host_owed(container, demo) == 42_500


def test_payout_webhook_arriving_too_early_is_retried(
    container: Container, demo: SeedResult, clock: FrozenClock, async_provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)  # queued, not yet sent
    payout = payout_of(container, booking_id)
    deliver_webhook(container, async_provider, EventKind.PAYOUT_SUCCEEDED, payout_id=str(payout.id))

    container.relay.drain()  # sends the payout; the early webhook is retried, then succeeds
    clock.advance(timedelta(minutes=10))
    container.relay.drain()
    assert status_of(container, booking_id) is S.PAID_OUT


def test_chargeback_blocks_the_payout(
    container: Container, demo: SeedResult, clock: FrozenClock, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    with transaction(container.session_factory) as session:
        capture_id = session.scalars(
            select(PaymentIntent.provider_capture_id).where(PaymentIntent.booking_id == booking_id)
        ).one()
    assert capture_id is not None
    deliver_webhook(container, provider, EventKind.CAPTURE_REVERSED, capture_id=capture_id)
    container.relay.drain()

    payout = payout_of(container, booking_id)
    assert payout.status is PayoutStatus.BLOCKED
    assert "chargeback" in (payout.review_reason or "")
    assert release_payouts_after_check_in(container, clock) == 0
