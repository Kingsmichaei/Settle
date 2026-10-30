"""Reliability tables: idempotency keys, the outbox, inbound webhooks and reconciliation runs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from settle.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from settle.db.enum_type import str_enum
from settle.db.types import UTCDateTime


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyRecord(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key"),)

    # The operation the key belongs to, e.g. "POST /v1/bookings" plus the caller.
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(str_enum(IdempotencyStatus), nullable=False)
    locked_until: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    DEAD = "dead"  # gave up after max attempts; needs a human


class OutboxEvent(UUIDPrimaryKey, Timestamps, Base):
    """Work to do after a transaction commits, written in that same transaction."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_due", "status", "next_attempt_at"),)

    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Enqueuing the same logical command twice is a no-op.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus), default=OutboxStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class WebhookEvent(UUIDPrimaryKey, Base):
    """Every distinct provider notification, stored once however often it is delivered."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id"),)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    deliveries: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_delivered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ReconciliationRun(UUIDPrimaryKey, Base):
    __tablename__ = "reconciliation_runs"

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    checks_run: Mapped[int] = mapped_column(Integer, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    summarized_by: Mapped[str] = mapped_column(String(100), nullable=False)
