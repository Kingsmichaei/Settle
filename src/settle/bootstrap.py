"""Composition root: builds every long-lived collaborator in one place.

The API, the worker, the CLI and the tests all start from ``build_container``, so wiring is
never duplicated and any collaborator can be swapped (a frozen clock, a fake provider or LLM).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine

from settle.ai.llm import GeminiLLM, LLMClient, NoLLM
from settle.config import Settings
from settle.core.clock import Clock, SystemClock
from settle.db.session import SessionFactory, make_engine, make_session_factory
from settle.domain.fx import StaticFxRates
from settle.idempotency.store import IdempotencyStore
from settle.outbox.relay import OutboxRelay, RetryPolicy
from settle.providers.base import PaymentProvider
from settle.providers.factory import build_provider
from settle.services.context import Services
from settle.services.handlers import PaymentHandlers


def build_llm(settings: Settings) -> LLMClient:
    if settings.ai_provider == "gemini":
        return GeminiLLM(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.ai_model,
            base_url=settings.gemini_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    return NoLLM()


@dataclass(slots=True)
class Container:
    settings: Settings
    engine: Engine
    session_factory: SessionFactory
    services: Services
    idempotency: IdempotencyStore = field(init=False)
    relay: OutboxRelay = field(init=False)

    def __post_init__(self) -> None:
        self.idempotency = IdempotencyStore(
            self.session_factory, self.services.clock, self.settings.idempotency_lock_seconds
        )
        self.relay = OutboxRelay(
            self.session_factory,
            PaymentHandlers(self.services).as_mapping(),
            self.services.clock,
            retry_policy=RetryPolicy(max_attempts=self.settings.outbox_max_attempts),
            batch_size=self.settings.outbox_batch_size,
        )

    @property
    def clock(self) -> Clock:
        return self.services.clock

    @property
    def provider(self) -> PaymentProvider:
        return self.services.provider

    def close(self) -> None:
        self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    clock: Clock | None = None,
    provider: PaymentProvider | None = None,
    llm: LLMClient | None = None,
    engine: Engine | None = None,
) -> Container:
    engine = engine or make_engine(settings.database_url)
    services = Services.build(
        settings,
        clock=clock or SystemClock(),
        provider=provider or build_provider(settings),
        fx=StaticFxRates(settings.fx_rates),
        llm=llm or build_llm(settings),
    )
    return Container(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        services=services,
    )
