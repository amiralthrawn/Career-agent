import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.database import create_app_engine, get_db
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

# A throw-away token generated for each test session: no token value is ever written in tests.
TEST_API_TOKEN = secrets.token_urlsafe(32)

SETTINGS_ENV_NAMES = (
    "APP_ENV",
    "APP_DEBUG",
    "DATABASE_URL",
    "PRIVATE_DATA_DIR",
    "CORS_ORIGINS",
    "API_TOKEN",
    "ALLOWED_HOSTS",
    "SEND_MODE",
    "SEND_ALLOWED_RECIPIENTS",
    "MAIL_FROM",
)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests independent from the developer's real .env / environment.

    The real `.env` (which holds the real API token and mail address) is never read by tests.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in SETTINGS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_TOKEN}"}


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite database with the schema created from the models.

    SQLite is used so tests need no PostgreSQL server; foreign keys are enforced to
    mimic PostgreSQL. The models only use portable types.
    """
    test_engine = create_app_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def client(engine: Engine, auth_headers: dict[str, str]) -> Iterator[TestClient]:
    """An authenticated client (valid API token) bound to the in-memory database."""
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, headers=auth_headers)


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


@pytest.fixture
def configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[], Session]]:
    """A file-based SQLite database wired through DATABASE_URL (as the scripts and the
    application startup use it). Returns a factory of sessions on that database."""
    from app.core.database import get_engine, get_session_factory

    url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    Base.metadata.create_all(get_engine())
    yield lambda: Session(get_engine(), expire_on_commit=False)
    get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


_LOOPBACK = {"127.0.0.1", "::1", "localhost", None, ""}


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to reach a non-local host fails the test.

    Loopback (127.0.0.1 / ::1) stays allowed: asyncio and the test client use it in-process.
    """
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if host not in _LOOPBACK:
            raise AssertionError("network access is forbidden in these tests")

    def connect(self: Any, address: Any) -> Any:
        check(address)
        return real_connect(self, address)

    def connect_ex(self: Any, address: Any) -> Any:
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK:
            raise AssertionError("network access is forbidden in these tests")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
