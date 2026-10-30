"""The contract every payment provider adapter must meet.

Every money-moving call takes a ``request_id``. Adapters must pass it to the provider as its
idempotency key (``PayPal-Request-Id`` for PayPal), so a retried call can never charge or pay
twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from settle.domain.money import Money


class ProviderError(Exception):
    """Base class for provider failures."""


class TransientProviderError(ProviderError):
    """Timeouts, 5xx, rate limits. The outcome is unknown; retry with the same request_id."""


class PermanentProviderError(ProviderError):
    """The provider definitively refused (declined card, invalid account). Do not retry."""


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    order_id: str
    approval_url: str


class PayoutState(StrEnum):
    SUCCEEDED = "succeeded"
    PENDING = "pending"  # accepted; the outcome arrives later by webhook


@dataclass(frozen=True, slots=True)
class PayoutSubmission:
    provider_payout_id: str
    state: PayoutState


class CaptureState(StrEnum):
    COMPLETED = "completed"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    REVERSED = "reversed"  # chargeback
    PENDING = "pending"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CaptureInfo:
    capture_id: str
    state: CaptureState
    amount: Money
    refunded: Money | None  # None when the provider doesn't report a running refund total


class EventKind(StrEnum):
    """Provider notifications, normalised to the few that change what we do."""

    ORDER_APPROVED = "order_approved"
    PAYOUT_SUCCEEDED = "payout_succeeded"
    PAYOUT_FAILED = "payout_failed"
    CAPTURE_REVERSED = "capture_reversed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_id: str
    event_type: str  # the provider's own name, e.g. "CHECKOUT.ORDER.APPROVED"
    kind: EventKind
    payload: dict[str, Any]
    order_id: str | None = None
    capture_id: str | None = None
    payout_id: uuid.UUID | None = None  # our payout id, echoed back by the provider
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def create_order(
        self, *, request_id: str, amount: Money, reference: str, return_url: str, cancel_url: str
    ) -> ProviderOrder:
        """Create an order the guest approves on the provider's side."""
        ...

    def authorize_order(self, *, request_id: str, order_id: str) -> str:
        """Reserve the guest's funds. Returns the authorization id."""
        ...

    def capture_authorization(
        self, *, request_id: str, authorization_id: str, amount: Money
    ) -> str:
        """Move reserved funds to the platform. Returns the capture id."""
        ...

    def void_authorization(self, *, request_id: str, authorization_id: str) -> None:
        """Release a reservation without charging."""
        ...

    def refund_capture(self, *, request_id: str, capture_id: str, amount: Money) -> str:
        """Return captured funds to the guest. Returns the refund id."""
        ...

    def send_payout(
        self, *, request_id: str, payout_id: uuid.UUID, recipient: str, amount: Money
    ) -> PayoutSubmission:
        """Pay a host. ``payout_id`` must come back in the payout's webhooks."""
        ...

    def get_capture(self, capture_id: str) -> CaptureInfo:
        """The provider's current view of a capture, for reconciliation."""
        ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """True only if the notification really came from the provider."""
        ...

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        """Turn a verified notification into a normalised event."""
        ...
