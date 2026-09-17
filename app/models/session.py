"""Engine and session plumbing.

The engine is built lazily and cached, so importing a model does not create a
database file as a side effect — tests bind their own in-memory engine instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.models.db import Base

__all__ = ["get_engine", "get_sessionmaker", "init_db", "session_scope"]


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ships with foreign keys *off*.

    Without this the ON DELETE CASCADE clauses are decoration and a `/profile`
    delete leaves orphaned analyses behind — the exact failure the privacy posture
    is meant to prevent.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def build_engine(database_url: str) -> Engine:
    """Create an engine for a URL, applying the SQLite-specific fixes."""
    is_sqlite = database_url.startswith("sqlite")
    in_memory = is_sqlite and ":memory:" in database_url

    if is_sqlite and not in_memory:
        path = Path(database_url.split("///", 1)[-1])
        path.parent.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {
        # The bot, FastAPI and `asyncio.to_thread` all touch sessions from
        # different threads.
        "connect_args": {"check_same_thread": False} if is_sqlite else {},
        "future": True,
    }
    if in_memory:
        # An in-memory database lives inside its *connection*. The default pool
        # hands each thread a new one, so a session used from a worker thread finds
        # an empty database with none of the tables in it — which shows up as a
        # baffling "no such table" the moment anything runs off the event loop.
        kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **kwargs)
    if is_sqlite:
        _enable_sqlite_foreign_keys(engine)
    return engine


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def init_db(engine: Engine | None = None) -> None:
    """Create any missing tables. Safe to call on every startup."""
    Base.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on anything else, always close."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
