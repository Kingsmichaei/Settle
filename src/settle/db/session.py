"""Engine and session factory. SQLite gets the pragmas it needs to be safe for this workload."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
    cursor.execute("PRAGMA journal_mode=WAL")  # lets the API and the worker share the file
    cursor.execute("PRAGMA busy_timeout=5000")  # wait for a lock instead of failing at once
    cursor.close()


def normalize_url(database_url: str) -> str:
    """Hosting platforms hand out ``postgres://`` URLs; SQLAlchemy needs a driver name."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    database_url = normalize_url(database_url)
    is_sqlite = database_url.startswith("sqlite")
    connect_args: dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(
        database_url, echo=echo, connect_args=connect_args, pool_pre_ping=not is_sqlite
    )
    if is_sqlite:
        event.listen(engine, "connect", _configure_sqlite)
    return engine


SessionFactory = sessionmaker[Session]


def make_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


@contextmanager
def transaction(factory: SessionFactory) -> Iterator[Session]:
    """Open a session, commit on success, roll back on any exception."""
    with factory() as session, session.begin():
        yield session
