"""Disputes: open, evidence, AI recommendation, operator decision, and the money that follows."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.core.errors import InvalidTransition, ValidationFailed
from settle.db.models import Dispute, DisputeParty, DisputeStatus, EvidenceKind
from settle.db.session import transaction
from settle.domain.money import Money
from settle.domain.states import BookingStatus as S
from settle.seed import SeedResult
from settle.services.bookings import get_booking
from settle.services.disputes import add_evidence, decide_dispute, open_dispute
from tests.support.flow import (
    book_and_pay,
    host_owed,
    ledger_is_balanced,
    payout_of,
    release_payouts_after_check_in,
    status_of,
)


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def start_dispute(container: Container, booking_id: uuid.UUID, claim: int = 10_000) -> uuid.UUID:
    with transaction(container.session_factory) as session:
        dispute = open_dispute(
            session,
            booking=get_booking(session, booking_id),
            reason="No hot water",
            claim=Money(claim, "USD"),
            statement="The boiler was broken all week.",
        )
        return dispute.id


def decide(container: Container, demo: SeedResult, dispute_id: uuid.UUID, refund: int) -> None:
    with transaction(container.session_factory) as session:
        decide_dispute(
            session,
            container.services,
            dispute=session.get_one(Dispute, dispute_id),
            decided_by=demo.admin,
            refund=Money(refund, "USD"),
            note="test",
        )


def test_dispute_freezes_the_payout(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    start_dispute(container, booking_id)
    assert status_of(container, booking_id) is S.DISPUTED
    assert release_payouts_after_check_in(container, clock) == 0


def test_partial_award_refunds_and_pays_the_host_the_rest(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    decide(container, demo, start_dispute(container, booking_id), refund=10_000)
    container.relay.drain()

    assert status_of(container, booking_id) is S.PARTIALLY_REFUNDED
    assert host_owed(container, demo) == 34_000  # 42,500 less 85% of the 10,000 refund
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    assert status_of(container, booking_id) is S.PAID_OUT
    assert ledger_is_balanced(container)


def test_host_wins_and_the_hold_resumes(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    decide(container, demo, start_dispute(container, booking_id), refund=0)
    assert status_of(container, booking_id) is S.HELD
    assert payout_of(container, booking_id).amount_minor == 42_500
    assert release_payouts_after_check_in(container, clock) == 1


def test_full_award_refunds_everything(container: Container, demo: SeedResult) -> None:
    booking_id = book_and_pay(container, demo)
    decide(container, demo, start_dispute(container, booking_id, claim=50_000), refund=50_000)
    container.relay.drain()
    assert status_of(container, booking_id) is S.REFUNDED
    assert host_owed(container, demo) == 0


def test_award_cannot_exceed_what_is_refundable(container: Container, demo: SeedResult) -> None:
    booking_id = book_and_pay(container, demo)
    dispute_id = start_dispute(container, booking_id)
    with pytest.raises(ValidationFailed):
        decide(container, demo, dispute_id, refund=50_001)


def test_claim_cannot_exceed_the_payment(container: Container, demo: SeedResult) -> None:
    booking_id = book_and_pay(container, demo)
    with pytest.raises(ValidationFailed):
        start_dispute(container, booking_id, claim=60_000)


def test_decided_dispute_is_closed(container: Container, demo: SeedResult) -> None:
    booking_id = book_and_pay(container, demo)
    dispute_id = start_dispute(container, booking_id)
    decide(container, demo, dispute_id, refund=0)
    with transaction(container.session_factory) as session, pytest.raises(InvalidTransition):
        add_evidence(
            session,
            dispute=session.get_one(Dispute, dispute_id),
            party=DisputeParty.HOST,
            kind=EvidenceKind.STATEMENT,
            content="Too late",
            url=None,
        )


def test_dispute_over_http_with_ai_recommendation(
    client: TestClient,
    container: Container,
    demo: SeedResult,
    guest_auth: dict[str, str],
    host_auth: dict[str, str],
    admin_auth: dict[str, str],
) -> None:
    booking_id = book_and_pay(container, demo)
    opened = client.post(
        f"/v1/bookings/{booking_id}/disputes",
        json={
            "reason": "Broken boiler",
            "claim": {"amount_minor": 20_000, "currency": "USD"},
            "statement": "No hot water for 4 nights",
        },
        headers={**guest_auth, **key()},
    )
    assert opened.status_code == 201, opened.text
    dispute_id = opened.json()["id"]

    evidence = client.post(
        f"/v1/disputes/{dispute_id}/evidence",
        json={"kind": "statement", "content": "The boiler was serviced last month."},
        headers={**host_auth, **key()},
    )
    assert evidence.status_code == 201
    assert {item["party"] for item in evidence.json()["evidence"]} == {"guest", "host"}

    outsider = client.post(
        "/v1/auth/token", json={"email": demo.host_ng.email, "password": "settle-demo"}
    ).json()["access_token"]
    denied = client.post(
        f"/v1/disputes/{dispute_id}/evidence",
        json={"kind": "statement", "content": "I am not involved"},
        headers={"Authorization": f"Bearer {outsider}", **key()},
    )
    assert denied.status_code == 403

    client.post(f"/v1/admin/disputes/{dispute_id}/recommend", headers={**admin_auth, **key()})
    container.relay.drain()
    dispute = client.get(f"/v1/disputes/{dispute_id}", headers=admin_auth).json()
    assert dispute["status"] == DisputeStatus.RECOMMENDED
    recommendation = dispute["recommendation"]
    assert recommendation["recommended_by"] == "rules"
    assert recommendation["refund"]["amount_minor"] == 10_000  # equal evidence: half the claim

    decided = client.post(
        f"/v1/admin/disputes/{dispute_id}/decide",
        json={"refund": recommendation["refund"], "note": "Agree with the mediator"},
        headers={**admin_auth, **key()},
    )
    assert decided.status_code == 200
    container.relay.drain()
    assert status_of(container, booking_id) is S.PARTIALLY_REFUNDED
