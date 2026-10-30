"""Runs due outbox events, retrying with exponential backoff and jitter.

Each event is handled in its own transaction: the handler's state changes and the event being
marked done commit together, so a crash mid-way simply leaves the event to be run again.
Handlers must therefore be idempotent, which the state machine and provider request ids ensure.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.clock import Clock
from settle.db.models import OutboxEvent, OutboxStatus
from settle.db.session import SessionFactory, transaction
from settle.outbox.topics import Topic

logger = logging.getLogger(__name__)

Handler = Callable[[Session, OutboxEvent], None]

MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 8
    base_delay: timedelta = timedelta(seconds=2)
    max_delay: timedelta = timedelta(minutes=5)

    def delay_for(self, attempt: int, rng: random.Random) -> timedelta:
        """Full-jitter exponential backoff: a random delay up to base * 2^attempt (capped)."""
        # Work in float seconds: base * 2**attempt overflows timedelta for large attempts.
        exponential = self.base_delay.total_seconds() * 2.0 ** min(max(attempt - 1, 0), 64)
        ceiling = min(self.max_delay.total_seconds(), exponential)
        return timedelta(seconds=rng.uniform(0, ceiling))


@dataclass(frozen=True, slots=True)
class RelayReport:
    succeeded: int = 0
    retried: int = 0
    dead: int = 0

    @property
    def processed(self) -> int:
        return self.succeeded + self.retried + self.dead


class OutboxRelay:
    def __init__(
        self,
        session_factory: SessionFactory,
        handlers: Mapping[Topic, Handler],
        clock: Clock,
        *,
        retry_policy: RetryPolicy | None = None,
        batch_size: int = 50,
        rng: random.Random | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = handlers
        self._clock = clock
        self._retry = retry_policy or RetryPolicy()
        self._batch_size = batch_size
        self._rng = rng or random.Random()

    def run_once(self) -> RelayReport:
        """Process every event that is due right now (up to one batch)."""
        succeeded = retried = dead = 0
        for event_id in self._due_event_ids():
            outcome = self._process(event_id)
            if outcome is OutboxStatus.DONE:
                succeeded += 1
            elif outcome is OutboxStatus.DEAD:
                dead += 1
            elif outcome is OutboxStatus.PENDING:
                retried += 1
        return RelayReport(succeeded, retried, dead)

    def drain(self, max_rounds: int = 100) -> RelayReport:
        """Run until nothing is due. Useful in tests and scripts, not in the live worker."""
        total = RelayReport()
        for _ in range(max_rounds):
            report = self.run_once()
            if report.processed == 0:
                break
            total = RelayReport(
                total.succeeded + report.succeeded,
                total.retried + report.retried,
                total.dead + report.dead,
            )
        return total

    def _due_event_ids(self) -> list[uuid.UUID]:
        with self._session_factory() as session:
            stmt = (
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.status == OutboxStatus.PENDING,
                    OutboxEvent.next_attempt_at <= self._clock.now(),
                )
                .order_by(OutboxEvent.next_attempt_at)
                .limit(self._batch_size)
            )
            return list(session.scalars(stmt))

    def _process(self, event_id: uuid.UUID) -> OutboxStatus | None:
        try:
            with transaction(self._session_factory) as session:
                # SKIP LOCKED lets several workers share the queue on Postgres; SQLite ignores it.
                event = session.scalar(
                    select(OutboxEvent)
                    .where(OutboxEvent.id == event_id, OutboxEvent.status == OutboxStatus.PENDING)
                    .with_for_update(skip_locked=True)
                )
                if event is None:
                    return None  # another worker took it
                handler = self._handlers[Topic(event.topic)]
                handler(session, event)
                event.status = OutboxStatus.DONE
                event.processed_at = self._clock.now()
                event.attempts += 1
                return OutboxStatus.DONE
        except Exception as exc:  # any failure: record it and schedule a retry
            logger.warning("Outbox event %s failed: %s", event_id, exc, exc_info=True)
            return self._record_failure(event_id, exc)

    def _record_failure(self, event_id: uuid.UUID, exc: Exception) -> OutboxStatus:
        with transaction(self._session_factory) as session:
            event = session.get_one(OutboxEvent, event_id)
            event.attempts += 1
            event.last_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            if event.attempts >= self._retry.max_attempts:
                event.status = OutboxStatus.DEAD
                logger.error("Outbox event %s is dead after %d attempts", event_id, event.attempts)
            else:
                event.next_attempt_at = self._clock.now() + self._retry.delay_for(
                    event.attempts, self._rng
                )
            return event.status
