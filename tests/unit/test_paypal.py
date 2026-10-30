"""The PayPal adapter against a scripted PayPal API (httpx MockTransport)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest

from settle.domain.money import Money
from settle.providers.base import (
    CaptureState,
    EventKind,
    PayoutState,
    PermanentProviderError,
    ProviderEvent,
    TransientProviderError,
)
from settle.providers.paypal import PayPalCredentials, PayPalProvider, to_paypal_amount

Responder = Callable[[httpx.Request], httpx.Response]
CREDS = PayPalCredentials("client-id", "client-secret", "WH-123")


class FakePayPal:
    """Records requests and answers them from a routing table."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Responder] = {}
        self.token_calls = 0

    def on(self, method: str, path: str, responder: Responder) -> None:
        self.routes[(method, path)] = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            self.token_calls += 1
            return httpx.Response(
                200, json={"access_token": f"T{self.token_calls}", "expires_in": 3600}
            )
        responder = self.routes.get((request.method, request.url.path))
        if responder is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})
        return responder(request)

    def last(self, path: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if r.url.path == path)


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def adapter(paypal: FakePayPal) -> PayPalProvider:
    return PayPalProvider(
        CREDS, base_url="https://paypal.test", transport=httpx.MockTransport(paypal)
    )


def ok(body: dict[str, object], status: int = 201) -> Responder:
    return lambda _request: httpx.Response(status, json=body)


def test_amounts_use_currency_minor_units() -> None:
    assert to_paypal_amount(Money(50_000, "USD")) == {"currency_code": "USD", "value": "500.00"}
    assert to_paypal_amount(Money(1_500, "JPY")) == {"currency_code": "JPY", "value": "1500"}


def test_create_order_sends_request_id_and_returns_the_approval_link(
    paypal: FakePayPal, adapter: PayPalProvider
) -> None:
    paypal.on(
        "POST",
        "/v2/checkout/orders",
        ok(
            {
                "id": "ORDER-1",
                "links": [
                    {"rel": "payer-action", "href": "https://paypal.test/checkoutnow?token=ORDER-1"}
                ],
            }
        ),
    )
    order = adapter.create_order(
        request_id="req-1",
        amount=Money(50_000, "USD"),
        reference="booking-1",
        return_url="https://app.test/return",
        cancel_url="https://app.test/cancel",
    )
    assert order.order_id == "ORDER-1"
    assert order.approval_url.endswith("token=ORDER-1")

    sent = paypal.last("/v2/checkout/orders")
    assert sent.headers["PayPal-Request-Id"] == "req-1"
    assert sent.headers["Authorization"] == "Bearer T1"
    body = json.loads(sent.content)
    assert body["intent"] == "AUTHORIZE"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "500.00"}
    context = body["payment_source"]["paypal"]["experience_context"]
    assert context["return_url"] == "https://app.test/return"


def test_authorize_capture_refund(paypal: FakePayPal, adapter: PayPalProvider) -> None:
    paypal.on(
        "POST",
        "/v2/checkout/orders/ORDER-1/authorize",
        ok(
            {
                "purchase_units": [
                    {"payments": {"authorizations": [{"id": "AUTH-1", "status": "CREATED"}]}}
                ]
            }
        ),
    )
    paypal.on("POST", "/v2/payments/authorizations/AUTH-1/capture", ok({"id": "CAP-1"}))
    paypal.on("POST", "/v2/payments/captures/CAP-1/refund", ok({"id": "REF-1"}))

    assert adapter.authorize_order(request_id="a", order_id="ORDER-1") == "AUTH-1"
    assert (
        adapter.capture_authorization(
            request_id="c", authorization_id="AUTH-1", amount=Money(50_000, "USD")
        )
        == "CAP-1"
    )
    assert (
        adapter.refund_capture(request_id="r", capture_id="CAP-1", amount=Money(100, "USD"))
        == "REF-1"
    )
    capture_body = json.loads(paypal.last("/v2/payments/authorizations/AUTH-1/capture").content)
    assert capture_body == {
        "amount": {"currency_code": "USD", "value": "500.00"},
        "final_capture": True,
    }


def test_token_is_cached(paypal: FakePayPal, adapter: PayPalProvider) -> None:
    paypal.on("POST", "/v2/payments/authorizations/AUTH-1/void", ok({}, 204))
    adapter.void_authorization(request_id="v1", authorization_id="AUTH-1")
    adapter.void_authorization(request_id="v2", authorization_id="AUTH-1")
    assert paypal.token_calls == 1


def test_expired_token_is_refreshed_once(paypal: FakePayPal, adapter: PayPalProvider) -> None:
    calls = {"n": 0}

    def flaky(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401 if calls["n"] == 1 else 201, json={"id": "CAP-9"})

    paypal.on("POST", "/v2/payments/authorizations/AUTH-1/capture", flaky)
    assert (
        adapter.capture_authorization(
            request_id="c", authorization_id="AUTH-1", amount=Money(1, "USD")
        )
        == "CAP-9"
    )
    assert paypal.token_calls == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_statuses_are_transient(
    paypal: FakePayPal, adapter: PayPalProvider, status: int
) -> None:
    paypal.on("POST", "/v2/payments/captures/CAP-1/refund", ok({}, status))
    with pytest.raises(TransientProviderError):
        adapter.refund_capture(request_id="r", capture_id="CAP-1", amount=Money(1, "USD"))


def test_client_errors_are_permanent_with_paypal_details(
    paypal: FakePayPal, adapter: PayPalProvider
) -> None:
    paypal.on(
        "POST",
        "/v2/payments/captures/CAP-1/refund",
        ok(
            {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed.",
                "details": [{"issue": "CAPTURE_FULLY_REFUNDED"}],
            },
            422,
        ),
    )
    with pytest.raises(PermanentProviderError, match="CAPTURE_FULLY_REFUNDED"):
        adapter.refund_capture(request_id="r", capture_id="CAP-1", amount=Money(1, "USD"))


def test_network_failure_is_transient(adapter: PayPalProvider, paypal: FakePayPal) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    paypal.on("POST", "/v2/payments/captures/CAP-1/refund", boom)
    with pytest.raises(TransientProviderError):
        adapter.refund_capture(request_id="r", capture_id="CAP-1", amount=Money(1, "USD"))


def test_payout_uses_our_payout_id_as_the_item_reference(
    paypal: FakePayPal, adapter: PayPalProvider
) -> None:
    paypal.on(
        "POST",
        "/v1/payments/payouts",
        ok({"batch_header": {"payout_batch_id": "BATCH-1", "batch_status": "PENDING"}}),
    )
    payout_id = uuid.uuid4()
    submission = adapter.send_payout(
        request_id="p-1",
        payout_id=payout_id,
        recipient="host@example.com",
        amount=Money(39_100, "EUR"),
    )
    assert submission.state is PayoutState.PENDING
    assert submission.provider_payout_id == "BATCH-1"
    body = json.loads(paypal.last("/v1/payments/payouts").content)
    assert body["sender_batch_header"]["sender_batch_id"] == "p-1"
    assert body["items"][0]["sender_item_id"] == str(payout_id)
    assert body["items"][0]["amount"] == {"value": "391.00", "currency": "EUR"}


def test_get_capture(paypal: FakePayPal, adapter: PayPalProvider) -> None:
    paypal.on(
        "GET",
        "/v2/payments/captures/CAP-1",
        ok(
            {
                "id": "CAP-1",
                "status": "PARTIALLY_REFUNDED",
                "amount": {"currency_code": "USD", "value": "500.00"},
            },
            200,
        ),
    )
    info = adapter.get_capture("CAP-1")
    assert info.state is CaptureState.PARTIALLY_REFUNDED
    assert info.amount == Money(50_000, "USD")


WEBHOOK_HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.paypal.com/cert",
    "paypal-transmission-id": "tx-1",
    "paypal-transmission-sig": "sig",
    "paypal-transmission-time": "2026-11-01T09:00:00Z",
}


@pytest.mark.parametrize(("status", "expected"), [("SUCCESS", True), ("FAILURE", False)])
def test_webhook_verification_asks_paypal(
    paypal: FakePayPal, adapter: PayPalProvider, status: str, expected: bool
) -> None:
    paypal.on(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        ok({"verification_status": status}, 200),
    )
    body = json.dumps({"id": "WH-EVT-1", "event_type": "CHECKOUT.ORDER.APPROVED"}).encode()
    assert adapter.verify_webhook(WEBHOOK_HEADERS, body) is expected
    sent = json.loads(paypal.last("/v1/notifications/verify-webhook-signature").content)
    assert sent["webhook_id"] == "WH-123"
    assert sent["transmission_id"] == "tx-1"


def test_webhook_without_paypal_headers_is_rejected_locally(
    paypal: FakePayPal, adapter: PayPalProvider
) -> None:
    assert not adapter.verify_webhook({}, b"{}")
    assert not paypal.requests  # no call to PayPal at all


def test_parse_webhooks() -> None:
    adapter = PayPalProvider(CREDS, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    payout_id = uuid.uuid4()

    def parse(event_type: str, resource: dict[str, object]) -> ProviderEvent:
        return adapter.parse_webhook(
            json.dumps({"id": "E1", "event_type": event_type, "resource": resource}).encode()
        )

    approved = parse("CHECKOUT.ORDER.APPROVED", {"id": "ORDER-1"})
    assert approved.kind is EventKind.ORDER_APPROVED
    assert approved.order_id == "ORDER-1"
    paid = parse(
        "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"payout_item": {"sender_item_id": str(payout_id)}}
    )
    assert paid.kind is EventKind.PAYOUT_SUCCEEDED
    assert paid.payout_id == payout_id
    failed = parse(
        "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
        {"payout_item": {"sender_item_id": str(payout_id)}, "transaction_status": "UNCLAIMED"},
    )
    assert failed.kind is EventKind.PAYOUT_FAILED
    assert failed.reason == "UNCLAIMED"
    reversed_ = parse("PAYMENT.CAPTURE.REVERSED", {"id": "CAP-1"})
    assert reversed_.kind is EventKind.CAPTURE_REVERSED
    assert parse("BILLING.PLAN.CREATED", {}).kind is EventKind.IGNORED
