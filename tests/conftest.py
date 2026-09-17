"""Shared fixtures.

The whole suite runs with no API key, no bot token and no network — the same
property Phase 1 had, kept as the phases add I/O. The database is a throwaway file
per session rather than `:memory:`, because FastAPI's TestClient runs the app on a
second thread and each thread would otherwise get its own empty in-memory database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models.db import Base
from app.models.session import build_engine


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point every cached singleton at a temporary database, and at no bot."""
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["TELEGRAM_MODE"] = "polling"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["LLM_PROVIDER"] = "mock"

    from app.config import get_settings
    from app.models.session import get_engine, get_sessionmaker

    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()

    yield

    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()


@pytest.fixture
def session() -> Iterator[Session]:
    """A fresh, empty database per test. No cross-test contamination, ever."""
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session
    engine.dispose()
