"""SQLAlchemy engine and session management (lazy: no connection at import time)."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_app_engine(url: str, **kwargs: Any) -> Engine:
    """Create an engine with sound transaction semantics on every supported database.

    PostgreSQL (production) needs nothing special. For SQLite (tests, local tools) we use the
    PEP 249 mode of Python >= 3.12 (`autocommit=False`): a transaction is always open, so
    savepoints behave correctly. Without it pysqlite does not emit BEGIN before a SAVEPOINT and
    releasing the outermost savepoint silently COMMITS: a rolled-back import preview would
    persist and "one savepoint per row" would not be atomic. Foreign keys are enforced too, as
    they are in PostgreSQL.
    """
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True, **kwargs)

    connect_args = {**kwargs.pop("connect_args", {}), "autocommit": False}
    engine = create_engine(url, connect_args=connect_args, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.autocommit = True  # this pragma is a no-op inside a transaction
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.autocommit = False

    return engine


@lru_cache
def get_engine() -> Engine:
    return create_app_engine(get_settings().require_database_url())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    with get_session_factory()() as session:
        yield session
