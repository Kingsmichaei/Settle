"""PayPal adapter over the REST APIs (Orders v2, Payments v2, Payouts v1, Webhooks v1).

* Every money-moving call sends ``PayPal-Request-Id`` = our ``request_id``, so PayPal itself
  deduplicates retries.
* Timeouts, connection errors, 429 and 5xx become ``TransientProviderError`` (retry later);
  other 4xx become ``PermanentProviderError``. A 401 refreshes the token once and retries.
* Webhooks are verified by PayPal's verify-webhook-signature API before anything is trusted.

Docs: https://developer.paypal.com/api/rest/
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from settle.domain.money import Money, minor_unit_exponent
from settle.providers.base import (
    CaptureInfo,
    CaptureState,
    EventKind,
    PayoutState,
    PayoutSubmission,
    PermanentProviderError,
    ProviderEvent,
    ProviderOrder,
    TransientProviderError,
)

logger = logging.getLogger(__name__)

TOKEN_REFRESH_MARGIN_SECONDS = 60
HTTP_UNAUTHORIZED = 401
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
HTTP_CLIENT_ERROR = 400

_PAYOUT_FAILURE_EVENTS = {
    "PAYMENT.PAYOUTS-ITEM.FAILED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.DENIED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.CANCELED",
}

_CAPTURE_STATES = {
    "COMPLETED": CaptureState.COMPLETED,
    "PARTIALLY_REFUNDED": CaptureState.PARTIALLY_REFUNDED,
    "REFUNDED": CaptureState.REFUNDED,
    "PENDING": CaptureState.PENDING,
}


def to_paypal_amount(money: Money) -> dict[str, str]:
    exponent = minor_unit_exponent(money.currency)
    return {"currency_code": money.currency, "value": f"{money.to_decimal():.{exponent}f}"}


def from_paypal_amount(amount: Mapping[str, Any]) -> Money:
    currency = str(amount.get("currency_code") or amount.get("currency"))
    return Money.from_decimal(Decimal(str(amount["value"])), currency)


@dataclass(frozen=True, slots=True)
class PayPalCredentials:
    client_id: str
    client_secret: str
    webhook_id: str


class PayPalProvider:
    name = "paypal"

    def __init__(
        self,
        credentials: PayPalCredentials,
        *,
        base_url: str = "https://api-m.sandbox.paypal.com",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        brand_name: str = "Settle",
    ) -> None:
        self._credentials = credentials
        self._brand_name = brand_name
        self._http = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # ---- orders and payments ------------------------------------------------------------

    def create_order(
        self, *, request_id: str, amount: Money, reference: str, return_url: str, cancel_url: str
    ) -> ProviderOrder:
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": reference,
                    "custom_id": reference,
                    "amount": to_paypal_amount(amount),
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": self._brand_name,
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "CONTINUE",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }
        data = self._request("POST", "/v2/checkout/orders", json_body=body, request_id=request_id)
        links = {link.get("rel"): link.get("href") for link in data.get("links", [])}
        approval_url = links.get("payer-action") or links.get("approve")
        if not approval_url:
            raise PermanentProviderError("PayPal returned an order without an approval link")
        return ProviderOrder(order_id=str(data["id"]), approval_url=str(approval_url))

    def authorize_order(self, *, request_id: str, order_id: str) -> str:
        data = self._request(
            "POST", f"/v2/checkout/orders/{order_id}/authorize", json_body={}, request_id=request_id
        )
        try:
            authorization = data["purchase_units"][0]["payments"]["authorizations"][0]
        except (KeyError, IndexError) as exc:
            raise PermanentProviderError("PayPal authorized the order without an id") from exc
        if authorization.get("status") not in {"CREATED", "CAPTURED", "PENDING"}:
            raise PermanentProviderError(f"Authorization is {authorization.get('status')}")
        return str(authorization["id"])

    def capture_authorization(
        self, *, request_id: str, authorization_id: str, amount: Money
    ) -> str:
        data = self._request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            json_body={"amount": to_paypal_amount(amount), "final_capture": True},
            request_id=request_id,
        )
        return str(data["id"])

    def void_authorization(self, *, request_id: str, authorization_id: str) -> None:
        self._request(
            "POST", f"/v2/payments/authorizations/{authorization_id}/void", request_id=request_id
        )

    def refund_capture(self, *, request_id: str, capture_id: str, amount: Money) -> str:
        data = self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json_body={"amount": to_paypal_amount(amount)},
            request_id=request_id,
        )
        return str(data["id"])

    def get_capture(self, capture_id: str) -> CaptureInfo:
        data = self._request("GET", f"/v2/payments/captures/{capture_id}")
        return CaptureInfo(
            capture_id=capture_id,
            state=_CAPTURE_STATES.get(str(data.get("status")), CaptureState.OTHER),
            amount=from_paypal_amount(data["amount"]),
            refunded=None,
        )

    # ---- payouts ------------------------------------------------------------------------

    def send_payout(
        self, *, request_id: str, payout_id: uuid.UUID, recipient: str, amount: Money
    ) -> PayoutSubmission:
        exponent = minor_unit_exponent(amount.currency)
        body = {
            "sender_batch_header": {
                "sender_batch_id": request_id,
                "email_subject": f"You have a payout from {self._brand_name}",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": recipient,
                    "sender_item_id": str(payout_id),
                    "amount": {
                        "value": f"{amount.to_decimal():.{exponent}f}",
                        "currency": amount.currency,
                    },
                    "note": f"Payout for booking hosted on {self._brand_name}",
                }
            ],
        }
        data = self._request("POST", "/v1/payments/payouts", json_body=body, request_id=request_id)
        header = data.get("batch_header", {})
        status = str(header.get("batch_status", "PENDING"))
        if status in {"DENIED", "CANCELED"}:
            raise PermanentProviderError(f"PayPal payout batch {status.lower()}")
        state = PayoutState.SUCCEEDED if status == "SUCCESS" else PayoutState.PENDING
        return PayoutSubmission(str(header["payout_batch_id"]), state)

    # ---- webhooks -----------------------------------------------------------------------

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        lowered = {key.lower(): value for key, value in headers.items()}
        required = (
            "paypal-auth-algo",
            "paypal-cert-url",
            "paypal-transmission-id",
            "paypal-transmission-sig",
            "paypal-transmission-time",
        )
        if not all(lowered.get(name) for name in required):
            return False
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            return False
        data = self._request(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            json_body={
                "auth_algo": lowered["paypal-auth-algo"],
                "cert_url": lowered["paypal-cert-url"],
                "transmission_id": lowered["paypal-transmission-id"],
                "transmission_sig": lowered["paypal-transmission-sig"],
                "transmission_time": lowered["paypal-transmission-time"],
                "webhook_id": self._credentials.webhook_id,
                "webhook_event": event,
            },
        )
        return data.get("verification_status") == "SUCCESS"

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        raw: dict[str, Any] = json.loads(body)
        event_type = str(raw.get("event_type", ""))
        resource: dict[str, Any] = raw.get("resource", {})

        def event(kind: EventKind, **fields: Any) -> ProviderEvent:
            return ProviderEvent(
                event_id=str(raw["id"]), event_type=event_type, kind=kind, payload=raw, **fields
            )

        if event_type == "CHECKOUT.ORDER.APPROVED":
            return event(EventKind.ORDER_APPROVED, order_id=resource.get("id"))
        if event_type == "PAYMENT.CAPTURE.REVERSED":
            return event(EventKind.CAPTURE_REVERSED, capture_id=resource.get("id"))
        if event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
            payout_id = _uuid_or_none(resource.get("payout_item", {}).get("sender_item_id"))
            if event_type == "PAYMENT.PAYOUTS-ITEM.SUCCEEDED":
                return event(EventKind.PAYOUT_SUCCEEDED, payout_id=payout_id)
            if event_type in _PAYOUT_FAILURE_EVENTS:
                errors = resource.get("errors") or {}
                reason = errors.get("message") or resource.get("transaction_status") or event_type
                return event(EventKind.PAYOUT_FAILED, payout_id=payout_id, reason=str(reason))
        return event(EventKind.IGNORED)

    # ---- HTTP plumbing ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self._access_token(force_refresh=attempt > 0)}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
            if request_id is not None:
                headers["PayPal-Request-Id"] = request_id
            try:
                response = self._http.request(method, path, json=json_body, headers=headers)
            except httpx.TimeoutException as exc:
                raise TransientProviderError(f"PayPal timed out on {method} {path}") from exc
            except httpx.TransportError as exc:
                raise TransientProviderError(f"Could not reach PayPal: {exc}") from exc
            if response.status_code == HTTP_UNAUTHORIZED and attempt == 0:
                continue  # token expired early; refresh once
            return self._parse(response, method, path)
        raise PermanentProviderError("PayPal rejected our credentials")

    @staticmethod
    def _parse(response: httpx.Response, method: str, path: str) -> dict[str, Any]:
        status = response.status_code
        if status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR:
            raise TransientProviderError(f"PayPal returned {status} for {method} {path}")
        if status >= HTTP_CLIENT_ERROR:
            raise PermanentProviderError(_describe_error(response))
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _access_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if not force_refresh and self._token and time.monotonic() < self._token_expires_at:
                return self._token
            try:
                response = self._http.post(
                    "/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self._credentials.client_id, self._credentials.client_secret),
                )
            except httpx.HTTPError as exc:
                raise TransientProviderError(f"Could not get a PayPal token: {exc}") from exc
            data = self._parse(response, "POST", "/v1/oauth2/token")
            self._token = str(data["access_token"])
            lifetime = float(data.get("expires_in", 300)) - TOKEN_REFRESH_MARGIN_SECONDS
            self._token_expires_at = time.monotonic() + max(lifetime, 0)
            return self._token


def _describe_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"PayPal returned {response.status_code}"
    name = data.get("name") or data.get("error") or response.status_code
    details = data.get("details") or []
    issue = details[0].get("issue") if details and isinstance(details[0], dict) else None
    message = data.get("message") or data.get("error_description") or ""
    return " ".join(str(part) for part in (name, issue, message) if part)


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
