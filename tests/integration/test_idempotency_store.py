from __future__ import annotations

from datetime import timedelta

import pytest

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.core.errors import IdempotencyKeyReused, RequestInProgress
from settle.idempotency.store import Claim, StoredResponse, fingerprint

SCOPE = "POST /v1/test"


def test_first_request_claims_the_key(container: Container) -> None:
    assert isinstance(container.idempotency.claim(SCOPE, "k-1", "hash-a"), Claim)


def test_completed_request_is_replayed(container: Container) -> None:
    store = container.idempotency
    claim = store.claim(SCOPE, "k-1", "hash-a")
    assert isinstance(claim, Claim)
    store.complete(claim, 201, {"id": "abc"})

    assert store.claim(SCOPE, "k-1", "hash-a") == StoredResponse(201, {"id": "abc"})


def test_same_key_in_another_scope_is_independent(container: Container) -> None:
    container.idempotency.claim(SCOPE, "k-1", "hash-a")
    assert isinstance(container.idempotency.claim("POST /v1/other", "k-1", "hash-b"), Claim)


def test_different_body_is_rejected(container: Container) -> None:
    container.idempotency.claim(SCOPE, "k-1", "hash-a")
    with pytest.raises(IdempotencyKeyReused):
        container.idempotency.claim(SCOPE, "k-1", "hash-b")


def test_concurrent_retry_is_told_to_wait(container: Container) -> None:
    container.idempotency.claim(SCOPE, "k-1", "hash-a")
    with pytest.raises(RequestInProgress):
        container.idempotency.claim(SCOPE, "k-1", "hash-a")


def test_abandoned_claim_can_be_taken_over_after_its_lock_expires(
    container: Container, clock: FrozenClock
) -> None:
    container.idempotency.claim(SCOPE, "k-1", "hash-a")  # the first attempt "crashes"
    clock.advance(timedelta(seconds=container.settings.idempotency_lock_seconds + 1))
    assert isinstance(container.idempotency.claim(SCOPE, "k-1", "hash-a"), Claim)


def test_released_claim_can_be_reused(container: Container) -> None:
    store = container.idempotency
    claim = store.claim(SCOPE, "k-1", "hash-a")
    assert isinstance(claim, Claim)
    store.release(claim)
    assert isinstance(store.claim(SCOPE, "k-1", "hash-b"), Claim)


def test_fingerprint_ignores_key_order() -> None:
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
