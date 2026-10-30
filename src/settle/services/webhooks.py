"""Inbound provider webhooks: verify, store once, process in the background."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.core.errors import Unauthenticated, ValidationFailed
from settle.db.models import WebhookEvent
from settle.outbox.publisher import enqueue
from settle.outbox.topics import Topic
from settle.services.context import Services


@dataclass(frozen=True, slots=True)
class WebhookReceipt:
    event_id: str
    duplicate: bool


def receive_webhook(
    session: Session, services: Services, *, headers: Mapping[str, str], body: bytes
) -> WebhookReceipt:
    """Acknowledge fast. Duplicates (providers retry until they get a 2xx) are counted, not
    reprocessed. Raises ``TransientProviderError`` if verification itself couldn't run, so the
    provider retries later."""
    provider = services.provider
    if not provider.verify_webhook(headers, body):
        raise Unauthenticated("Webhook signature could not be verified")
    try:
        notice = provider.parse_webhook(body)
    except (ValueError, KeyError) as exc:
        raise ValidationFailed(f"Malformed webhook: {exc}") from exc

    now = services.clock.now()
    existing = session.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == provider.name,
            WebhookEvent.provider_event_id == notice.event_id,
        )
    )
    if existing is not None:
        existing.deliveries += 1
        existing.last_delivered_at = now
        return WebhookReceipt(notice.event_id, duplicate=True)

    stored = WebhookEvent(
        provider=provider.name,
        provider_event_id=notice.event_id,
        event_type=notice.event_type,
        payload=notice.payload,
        received_at=now,
        last_delivered_at=now,
    )
    session.add(stored)
    session.flush()
    enqueue(
        session,
        topic=Topic.PROCESS_WEBHOOK,
        aggregate_id=notice.event_id[:64],
        payload={"webhook_event_id": str(stored.id)},
        dedupe_key=f"{Topic.PROCESS_WEBHOOK.value}:{provider.name}:{notice.event_id}"[:200],
        now=now,
    )
    return WebhookReceipt(notice.event_id, duplicate=False)
