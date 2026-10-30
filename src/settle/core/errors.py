"""Domain-level exceptions. The API layer maps these to HTTP problem responses."""

from __future__ import annotations


class SettleError(Exception):
    """Base class for every expected, domain-level failure."""

    code: str = "settle_error"


class ValidationFailed(SettleError):
    code = "validation_failed"


class NotFound(SettleError):
    code = "not_found"


class InvalidTransition(SettleError):
    code = "invalid_transition"


class CurrencyMismatch(SettleError):
    code = "currency_mismatch"


class UnbalancedEntry(SettleError):
    code = "unbalanced_entry"


class IdempotencyKeyReused(SettleError):
    """The same idempotency key was sent with a different request body."""

    code = "idempotency_key_reused"


class RequestInProgress(SettleError):
    """A request with this idempotency key is still being processed."""

    code = "request_in_progress"


class Unauthenticated(SettleError):
    code = "unauthenticated"


class Forbidden(SettleError):
    code = "forbidden"
