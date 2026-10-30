"""Adds outbox events inside the caller's transaction."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.db.models import OutboxEvent
from settle.outbox.topics import Topic


def enqueue(
    session: Session,
    *,
    topic: Topic,
    aggregate_id: str,
    payload: dict[str, Any],
    dedupe_key: str,
    now: datetime,
) -> OutboxEvent:
    """Queue a command to run after this transaction commits.

    ``dedupe_key`` names the logical command (e.g. ``capture:<booking id>``). Enqueuing the
    same command twice returns the existing event instead of creating a second one.
    """
    existing = session.scalar(select(OutboxEvent).where(OutboxEvent.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    event = OutboxEvent(
        topic=topic,
        aggregate_id=aggregate_id,
        payload=payload,
        dedupe_key=dedupe_key,
        next_attempt_at=now,
    )
    session.add(event)
    session.flush()
    return event
