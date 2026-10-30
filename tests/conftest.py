"""Shared fixtures: per-test database, frozen clock, fake provider, rules-only AI.

Set ``SETTLE_TEST_DATABASE_URL`` to run the suite against Postgres (CI does this).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from settle.api.app import create_app
from settle.bootstrap import Container, build_container
from settle.config import Settings
from settle.core.clock import FrozenClock
from settle.db.base import Base
from settle.db.session import make_engine, transaction
from settle.providers.fake import FakeProvider
from settle.seed import DEMO_PASSWORD, SeedResult, seed_demo_data
from tests.support.data import NOW


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return os.environ.get("SETTLE_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        platform_fee_bps=1500,
        payout_hold_hours=24,
        outbox_max_attempts=3,
    )


@pytest.fixture
def container(
    settings: Settings, clock: FrozenClock, provider: FakeProvider
) -> Iterator[Container]:
    engine = make_engine(settings.database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    built = build_container(settings, clock=clock, provider=provider, engine=engine)
    yield built
    Base.metadata.drop_all(engine)
    built.close()


@pytest.fixture
def session(container: Container) -> Iterator[Session]:
    with transaction(container.session_factory) as active:
        yield active


@pytest.fixture
def demo(container: Container) -> SeedResult:
    with transaction(container.session_factory) as active:
        return seed_demo_data(active)


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as test_client:
        yield test_client


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/v1/auth/token", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def guest_auth(client: TestClient, demo: SeedResult) -> dict[str, str]:
    return login(client, demo.guest.email)


@pytest.fixture
def host_auth(client: TestClient, demo: SeedResult) -> dict[str, str]:
    return login(client, demo.host.email)


@pytest.fixture
def admin_auth(client: TestClient, demo: SeedResult) -> dict[str, str]:
    return login(client, demo.admin.email)
