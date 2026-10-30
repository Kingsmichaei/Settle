"""HTTP contract: authentication, permissions, idempotency and problem responses."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.seed import SeedResult
from tests.conftest import login
from tests.support.data import CHECK_IN, CHECK_OUT
from tests.support.flow import book_and_pay, release_payouts_after_check_in


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def booking_body(demo: SeedResult, index: int = 0) -> dict[str, Any]:
    return {
        "listing_id": str(demo.listings[index].id),
        "check_in": CHECK_IN.isoformat(),
        "check_out": CHECK_OUT.isoformat(),
    }


def create(client: TestClient, demo: SeedResult, auth: dict[str, str]) -> dict[str, Any]:
    response = client.post("/v1/bookings", json=booking_body(demo), headers={**auth, **key()})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ---- auth -------------------------------------------------------------------------------


def test_health_reports_components(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body == {"status": "ok", "database": "ok", "provider": "fake", "ai": "rules"}


def test_login_returns_a_working_token(client: TestClient, demo: SeedResult) -> None:
    auth = login(client, demo.guest.email)
    me = client.get("/v1/me", headers=auth).json()
    assert me["email"] == demo.guest.email
    assert me["role"] == "guest"


@pytest.mark.parametrize(
    ("email", "password"),
    [("ada.guest@example.com", "wrong"), ("nobody@example.com", "settle-demo")],
)
def test_bad_credentials_get_the_same_answer(
    client: TestClient, demo: SeedResult, email: str, password: str
) -> None:
    response = client.post("/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect"


def test_protected_routes_need_a_token(client: TestClient) -> None:
    response = client.get("/v1/bookings")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/bookings", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


# ---- bookings ---------------------------------------------------------------------------


def test_listings_are_public(client: TestClient, demo: SeedResult) -> None:
    listings = client.get("/v1/listings").json()
    assert {item["title"] for item in listings} == {listing.title for listing in demo.listings}
    assert all(item["policy_summary"] for item in listings)


def test_create_booking_prices_from_the_listing(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    body = create(client, demo, guest_auth)
    assert body["status"] == "pending"
    assert body["guest_id"] == str(demo.guest.id)
    assert body["total"] == {"amount_minor": 50_000, "currency": "USD", "formatted": "USD 500.00"}


def test_only_guests_can_book(
    client: TestClient, demo: SeedResult, host_auth: dict[str, str]
) -> None:
    response = client.post("/v1/bookings", json=booking_body(demo), headers={**host_auth, **key()})
    assert response.status_code == 403


def test_retry_with_same_key_replays_the_first_response(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    headers = {**guest_auth, **key()}
    first = client.post("/v1/bookings", json=booking_body(demo), headers=headers)
    second = client.post("/v1/bookings", json=booking_body(demo), headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"


def test_same_key_with_a_different_body_is_rejected(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    headers = {**guest_auth, **key()}
    client.post("/v1/bookings", json=booking_body(demo), headers=headers)
    response = client.post("/v1/bookings", json=booking_body(demo, 1), headers=headers)
    assert response.status_code == 422
    assert response.json()["type"] == "idempotency_key_reused"


def test_writes_require_an_idempotency_key(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    assert (
        client.post("/v1/bookings", json=booking_body(demo), headers=guest_auth).status_code == 422
    )


def test_failed_request_releases_its_key(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    headers = {**guest_auth, **key()}
    bad = {**booking_body(demo), "check_out": CHECK_IN.isoformat()}
    assert client.post("/v1/bookings", json=bad, headers=headers).status_code == 422
    assert client.post("/v1/bookings", json=booking_body(demo), headers=headers).status_code == 201


def test_other_people_cannot_see_a_booking(
    client: TestClient, demo: SeedResult, guest_auth: dict[str, str]
) -> None:
    booking = create(client, demo, guest_auth)
    other_host = login(client, demo.host_ng.email)  # hosts a different listing
    assert client.get(f"/v1/bookings/{booking['id']}", headers=other_host).status_code == 403
    own_host = login(client, demo.host.email)
    assert client.get(f"/v1/bookings/{booking['id']}", headers=own_host).status_code == 200


def test_unknown_booking_is_a_problem_response(
    client: TestClient, guest_auth: dict[str, str]
) -> None:
    response = client.get(f"/v1/bookings/{uuid.uuid4()}", headers=guest_auth)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "not_found"


def test_full_flow_over_http(
    client: TestClient, demo: SeedResult, container: Container, guest_auth: dict[str, str]
) -> None:
    booking_id = create(client, demo, guest_auth)["id"]

    checkout = client.post(f"/v1/bookings/{booking_id}/checkout", headers={**guest_auth, **key()})
    assert checkout.status_code == 200
    assert "/sandbox/approve/" in checkout.json()["approval_url"]

    confirmed = client.post(f"/v1/bookings/{booking_id}/confirm", headers={**guest_auth, **key()})
    assert confirmed.status_code == 202
    assert confirmed.json()["risk"]["action"] == "allow"

    container.relay.drain()
    held = client.get(f"/v1/bookings/{booking_id}", headers=guest_auth).json()
    assert held["status"] == "held"
    assert held["payment"]["status"] == "captured"
    assert held["payout"]["status"] == "scheduled"
    assert held["payout"]["amount"]["amount_minor"] == 42_500

    trips = client.get("/v1/bookings", headers=guest_auth).json()
    assert [trip["id"] for trip in trips] == [booking_id]

    quote = client.get(f"/v1/bookings/{booking_id}/cancellation-quote", headers=guest_auth).json()
    assert quote["refund_percent"] == 100


def test_tokens_expire(client: TestClient, clock: FrozenClock, guest_auth: dict[str, str]) -> None:
    clock.advance(timedelta(hours=13))
    assert client.get("/v1/me", headers=guest_auth).status_code == 401


def test_cancel_a_paid_out_booking_is_a_conflict(
    client: TestClient, demo: SeedResult, container: Container, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)  # six weeks later
    container.relay.drain()
    guest_auth = login(client, demo.guest.email)  # earlier tokens have expired by now

    response = client.post(
        f"/v1/bookings/{booking_id}/cancel",
        json={"reason": "changed my mind"},
        headers={**guest_auth, **key()},
    )
    assert response.status_code == 409
    assert response.json()["type"] == "invalid_transition"


# ---- hosts and admin --------------------------------------------------------------------


def test_host_summary_and_assistant(
    client: TestClient, demo: SeedResult, container: Container, host_auth: dict[str, str]
) -> None:
    book_and_pay(container, demo)
    summary = client.get("/v1/hosts/me/summary", headers=host_auth).json()
    assert summary["owed"][0]["amount_minor"] == 42_500
    assert summary["payouts"][0]["payout"]["status"] == "scheduled"

    reply = client.post(
        "/v1/hosts/me/assistant", json={"question": "When is my next payout?"}, headers=host_auth
    ).json()
    assert "USD 425.00" in reply["answer"]
    assert reply["answered_by"] == "rules"


def test_admin_routes_are_admin_only(
    client: TestClient, guest_auth: dict[str, str], admin_auth: dict[str, str]
) -> None:
    assert client.get("/v1/admin/summary", headers=guest_auth).status_code == 403
    assert client.get("/v1/admin/summary", headers=admin_auth).status_code == 200


def test_admin_overview(
    client: TestClient, demo: SeedResult, container: Container, admin_auth: dict[str, str]
) -> None:
    book_and_pay(container, demo)
    summary = client.get("/v1/admin/summary", headers=admin_auth).json()
    assert summary["funds_held"] == [
        {"amount_minor": 50_000, "currency": "USD", "formatted": "USD 500.00"}
    ]
    assert summary["owed_to_hosts"][0]["amount_minor"] == 42_500
    assert summary["platform_revenue"][0]["amount_minor"] == 7_500

    run = client.post("/v1/admin/reconciliation/runs", headers=admin_auth)
    assert run.status_code == 201
    assert run.json()["findings"] == []
    assert client.get("/v1/admin/ledger/integrity", headers=admin_auth).json()["balanced"]
    assert client.get("/v1/admin/outbox", headers=admin_auth).json() == []
