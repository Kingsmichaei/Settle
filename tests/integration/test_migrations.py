"""The Alembic migrations must produce exactly the schema the models describe."""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from settle.cli import migrate
from settle.db.base import Base
from settle.db.session import make_engine


def test_migrations_match_the_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    migrate(url)

    engine = make_engine(url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert diff == []
