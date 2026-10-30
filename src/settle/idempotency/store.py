"""Stores the outcome of each idempotent request so retries replay it instead of re-running it.

Flow for a request with key K:

1. ``claim`` inserts K as in-progress in its own committed transaction. The unique
   constraint means only one concurrent request can win the claim.
2. The winner runs the operation, then ``complete`` stores the response.
3. A retry with K and the same body gets the stored response. A different body is rejected.
   A retry while K is still in progress is told to wait, unless the lock has expired
   (the first attempt crashed), in which case it may run again. That is safe because the
   operations themselves are guarded by the state machine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from settle.core.clock import Clock
from settle.core.errors import IdempotencyKeyReused, RequestInProgress
from settle.db.models import IdempotencyRecord, IdempotencyStatus
from settle.db.session import SessionFactory, transaction


def fingerprint(payload: Any) -> str:
    """Stable hash of a JSON-compatible request body."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Claim:
    scope: str
    key: str


class IdempotencyStore:
    def __init__(self, session_factory: SessionFactory, clock: Clock, lock_seconds: int) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._lock = timedelta(seconds=lock_seconds)

    def claim(self, scope: str, key: str, request_hash: str) -> Claim | StoredResponse:
        """Either claim the key (caller must run the operation) or return the stored response."""
        now = self._clock.now()
        try:
            with transaction(self._session_factory) as session:
                session.add(
                    IdempotencyRecord(
                        scope=scope,
                        key=key,
                        request_hash=request_hash,
                        status=IdempotencyStatus.IN_PROGRESS,
                        locked_until=now + self._lock,
                    )
                )
            return Claim(scope, key)
        except IntegrityError:
            pass  # the key exists; decide below

        with transaction(self._session_factory) as session:
            record = session.scalar(
                select(IdempotencyRecord)
                .where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
                .with_for_update()
            )
            if record is None:  # released between our two transactions; claim it again
                return self.claim(scope, key, request_hash)
            if record.request_hash != request_hash:
                raise IdempotencyKeyReused(
                    "This Idempotency-Key was already used with a different request body"
                )
            if record.status is IdempotencyStatus.COMPLETED:
                if record.response_status is None or record.response_body is None:
                    raise RuntimeError(f"Completed idempotency record {record.id} has no response")
                return StoredResponse(record.response_status, record.response_body)
            if record.locked_until > now:
                raise RequestInProgress("A request with this Idempotency-Key is still running")
            record.locked_until = now + self._lock  # the earlier attempt died; take over
            return Claim(scope, key)

    def complete(self, claim: Claim, status_code: int, body: dict[str, Any]) -> None:
        with transaction(self._session_factory) as session:
            record = session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.scope == claim.scope, IdempotencyRecord.key == claim.key
                )
            ).one()
            record.status = IdempotencyStatus.COMPLETED
            record.response_status = status_code
            record.response_body = body

    def release(self, claim: Claim) -> None:
        """Forget a claim whose operation failed, so the client can retry with the same key."""
        with transaction(self._session_factory) as session:
            session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.scope == claim.scope,
                    IdempotencyRecord.key == claim.key,
                    IdempotencyRecord.status == IdempotencyStatus.IN_PROGRESS,
                )
            )
