"""Risk scoring at approval time, and payout holds for high-risk bookings."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.db.models import PayoutStatus, RiskAction, RiskAssessment, User
from settle.db.session import transaction
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from tests.conftest import login
from tests.support.data import CHECK_IN
from tests.support.flow import book_and_pay, payout_of, release_payouts_after_check_in


def assessment(container: Container, booking_id: uuid.UUID) -> RiskAssessment:
    with transaction(container.session_factory) as session:
        found = session.scalars(
            select(RiskAssessment).where(RiskAssessment.booking_id == booking_id)
        ).one()
        session.expunge(found)
        return found


def make_guest_brand_new(container: Container, demo: SeedResult) -> None:
    with transaction(container.session_factory) as session:
        session.get_one(User, demo.guest.id).created_at = container.clock.now() - timedelta(hours=2)


def test_ordinary_booking_is_allowed(container: Container, demo: SeedResult) -> None:
    risk = assessment(container, book_and_pay(container, demo))
    assert risk.action is RiskAction.ALLOW
    assert risk.score == 0
    assert risk.explained_by == "rules"


def test_new_account_booking_last_minute_is_reviewed(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    clock.set(clock.now().replace(month=12, day=11))  # check-in tomorrow
    make_guest_brand_new(container, demo)
    risk = assessment(container, book_and_pay(container, demo))
    assert {signal["code"] for signal in risk.signals} == {"new_account", "last_minute"}
    assert risk.action is RiskAction.ALLOW  # 35 < review threshold of 40
    assert "new account" in risk.explanation


def test_high_risk_booking_holds_the_payout_until_approved(
    client: TestClient,
    container: Container,
    demo: SeedResult,
    clock: FrozenClock,
    provider: FakeProvider,
) -> None:
    clock.set(clock.now().replace(month=12, day=11))
    make_guest_brand_new(container, demo)
    provider.fail_next_permanent("authorize", "card declined")  # an earlier declined attempt
    book_and_pay(container, demo, listing_index=1)
    # New account + last minute + declined payment + high value + long stay: 75 points.
    booking_id = book_and_pay(
        container, demo, listing_index=2, check_in=CHECK_IN, check_out=CHECK_IN + timedelta(days=30)
    )
    risk = assessment(container, booking_id)
    assert risk.action is RiskAction.HOLD
    assert payout_of(container, booking_id).status is PayoutStatus.BLOCKED

    admin_auth = login(client, demo.admin.email)
    queue = client.get("/v1/admin/risk", headers=admin_auth).json()
    assert [item["id"] for item in queue] == [str(booking_id)]

    approved = client.post(
        f"/v1/admin/bookings/{booking_id}/approve-payout",
        headers={**admin_auth, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert approved.status_code == 202
    assert payout_of(container, booking_id).status is PayoutStatus.SCHEDULED
    assert client.get("/v1/admin/risk", headers=admin_auth).json() == []
    assert release_payouts_after_check_in(container, clock) == 1
