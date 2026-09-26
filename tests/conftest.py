from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import get_db
from app.main import create_app
from app.models import Base

# All identity data in tests is obviously synthetic (reserved ".invalid" domain, "Test" names)
# so it can never be mistaken for real candidate data.
CANDIDATE_PAYLOAD: dict[str, Any] = {
    "first_name": "Test",
    "last_name": "Candidate-Fixture",
    "email": "candidate.fixture@example.invalid",
    "headline": "Synthetic fixture candidate",
}


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests independent from the developer's real .env / environment."""
    for name in ("APP_ENV", "APP_DEBUG", "DATABASE_URL", "PRIVATE_DATA_DIR", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite database with the schema created from the models.

    SQLite is used so tests need no PostgreSQL server; foreign keys are enforced to
    mimic PostgreSQL. The models only use portable types.
    """
    test_engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(test_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)


@pytest.fixture
def candidate_payload() -> dict[str, Any]:
    return dict(CANDIDATE_PAYLOAD)


@pytest.fixture
def private_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throw-away private data directory: tests never touch the real data/private."""
    directory = tmp_path / "private"
    (directory / "documents").mkdir(parents=True)
    monkeypatch.setenv("PRIVATE_DATA_DIR", str(directory))
    get_settings.cache_clear()
    return directory
