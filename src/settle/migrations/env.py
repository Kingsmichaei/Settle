"""Alembic environment. Uses the app's Settings and model metadata as the single source of truth."""

from __future__ import annotations

from alembic import context

import settle.db.models  # noqa: F401  (registers every table on Base.metadata)
from settle.config import get_settings
from settle.db.base import Base
from settle.db.session import make_engine

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Allows programmatic overrides (tests, CLI) via config.set_main_option("sqlalchemy.url", ...).
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode to alter tables
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
