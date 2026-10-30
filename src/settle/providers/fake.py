"""An in-memory provider for development, demos and failure-injection tests.

It behaves like a real provider in the ways that matter for correctness:

* Repeating a call with the same ``request_id`` returns the original result and moves no
  money again (like ``PayPal-Request-Id``).
* Failures can be scheduled per operation, including the nasty case where the provider
  succeeds but the response is lost (``lose_response=True``).
* Webhooks are signed with a shared secret (HMAC-SHA256), so signature checks are exercised.
* Payouts can complete immediately or asynchronously (confirmed later by webhook).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

from settle.domain.money import Money
from settle.providers.base import (
    CaptureInfo,
    CaptureState,
    EventKind,
    PayoutState,
    PayoutSubmission,
    PermanentProviderError,
    ProviderError,
    ProviderEvent,
    ProviderOrder,
    TransientProviderError,
)

Operation = Literal["create_order", "authorize", "capture", "void", "refund", "payout"]
T = TypeVar("T")

SIGNATURE_HEADER = "x-fake-signature"


@dataclass(frozen=True, slots=True)
class _ScheduledFailure:
    error: ProviderError
    lose_response: bool


@dataclass(slots=True)
class _Capture:
    amount: Money
    refunded: Money
    reversed: bool = False


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        webhook_secret: str = "fake-webhook-secret",
        approval_base_url: str = "http://localhost:5173/sandbox/approve",
        async_payouts: bool = False,
    ) -> None:
        self._secret = webhook_secret.encode()
        self._approval_base_url = approval_base_url.rstrip("/")
        self.async_payouts = async_payouts
        self._lock = threading.Lock()
        self._results: dict[tuple[Operation, str], object] = {}
        self._failures: dict[Operation, deque[_ScheduledFailure]] = defaultdict(deque)
        self._captures: dict[str, _Capture] = {}
        # How many times each operation actually executed (not counting idempotent replays).
        self.executions: Counter[Operation] = Counter()

    # ---- failure injection --------------------------------------------------------------

    def fail_next(
        self, operation: Operation, error: ProviderError, *, lose_response: bool = False
    ) -> None:
        """Make the next call to ``operation`` fail.

        With ``lose_response=True`` the operation *succeeds* at the provider, then the caller
        receives ``error``, which is how a network timeout after success looks in real life.
        """
        self._failures[operation].append(_ScheduledFailure(error, lose_response))

    def fail_next_transient(self, operation: Operation, *, lose_response: bool = False) -> None:
        self.fail_next(
            operation, TransientProviderError("simulated timeout"), lose_response=lose_response
        )

    def fail_next_permanent(self, operation: Operation, reason: str = "simulated decline") -> None:
        self.fail_next(operation, PermanentProviderError(reason))

    def reverse_capture(self, capture_id: str) -> None:
        """Simulate a chargeback on the provider side."""
        self._captures[capture_id].reversed = True

    # ---- webhooks (test and demo helpers) -----------------------------------------------

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def build_webhook(
        self, kind: EventKind, *, event_id: str | None = None, **data: Any
    ) -> tuple[bytes, dict[str, str]]:
        """A signed notification body and headers, as the provider would send them."""
        body = json.dumps(
            {"id": event_id or f"EVT-{uuid.uuid4().hex[:12].upper()}", "kind": kind, "data": data},
            sort_keys=True,
        ).encode()
        return body, {SIGNATURE_HEADER: self.sign(body), "content-type": "application/json"}

    # ---- PaymentProvider ----------------------------------------------------------------

    def create_order(
        self, *, request_id: str, amount: Money, reference: str, return_url: str, cancel_url: str
    ) -> ProviderOrder:
        def make() -> ProviderOrder:
            order_id = self._new_id("ORDER")
            return ProviderOrder(order_id, f"{self._approval_base_url}/{order_id}?ref={reference}")

        return self._run("create_order", request_id, make)

    def authorize_order(self, *, request_id: str, order_id: str) -> str:
        return self._run("authorize", request_id, lambda: self._new_id("AUTH"))

    def capture_authorization(
        self, *, request_id: str, authorization_id: str, amount: Money
    ) -> str:
        def make() -> str:
            capture_id = self._new_id("CAPTURE")
            self._captures[capture_id] = _Capture(amount, Money.zero(amount.currency))
            return capture_id

        return self._run("capture", request_id, make)

    def void_authorization(self, *, request_id: str, authorization_id: str) -> None:
        self._run("void", request_id, lambda: None)

    def refund_capture(self, *, request_id: str, capture_id: str, amount: Money) -> str:
        def make() -> str:
            capture = self._captures.get(capture_id)
            if capture is not None:
                capture.refunded += amount
            return self._new_id("REFUND")

        return self._run("refund", request_id, make)

    def send_payout(
        self, *, request_id: str, payout_id: uuid.UUID, recipient: str, amount: Money
    ) -> PayoutSubmission:
        state = PayoutState.PENDING if self.async_payouts else PayoutState.SUCCEEDED
        return self._run(
            "payout", request_id, lambda: PayoutSubmission(self._new_id("PAYOUT"), state)
        )

    def get_capture(self, capture_id: str) -> CaptureInfo:
        capture = self._captures.get(capture_id)
        if capture is None:
            raise PermanentProviderError(f"Unknown capture {capture_id}")
        if capture.reversed:
            state = CaptureState.REVERSED
        elif capture.refunded.is_zero:
            state = CaptureState.COMPLETED
        elif capture.refunded == capture.amount:
            state = CaptureState.REFUNDED
        else:
            state = CaptureState.PARTIALLY_REFUNDED
        return CaptureInfo(capture_id, state, capture.amount, capture.refunded)

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        signature = {k.lower(): v for k, v in headers.items()}.get(SIGNATURE_HEADER, "")
        return hmac.compare_digest(signature, self.sign(body))

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        raw = json.loads(body)
        data: dict[str, Any] = raw.get("data", {})
        payout_id = data.get("payout_id")
        return ProviderEvent(
            event_id=str(raw["id"]),
            event_type=str(raw["kind"]),
            kind=EventKind(raw["kind"]),
            payload=raw,
            order_id=data.get("order_id"),
            capture_id=data.get("capture_id"),
            payout_id=uuid.UUID(payout_id) if payout_id else None,
            reason=data.get("reason"),
        )

    # ---- internals ----------------------------------------------------------------------

    def _run(self, operation: Operation, request_id: str, execute: Callable[[], T]) -> T:
        with self._lock:
            key = (operation, request_id)
            if key in self._results:
                return cast(T, self._results[key])

            failure = self._failures[operation].popleft() if self._failures[operation] else None
            if failure is not None and not failure.lose_response:
                raise failure.error

            result = execute()
            self._results[key] = result
            self.executions[operation] += 1

            if failure is not None:
                raise failure.error
            return result

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"
