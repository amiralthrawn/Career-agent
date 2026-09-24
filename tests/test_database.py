import pytest

from app.core.config import ConfigurationError
from app.core.database import get_engine
from app.models import Base


def test_importing_database_module_does_not_connect() -> None:
    # Reaching this point means `app.core.database` imported without DATABASE_URL.
    assert Base.metadata is not None


def test_engine_requires_database_url() -> None:
    get_engine.cache_clear()
    with pytest.raises(ConfigurationError):
        get_engine()


def test_engine_is_created_lazily_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost:5432/unused")
    get_engine.cache_clear()
    try:
        engine = get_engine()
        assert engine.dialect.name == "postgresql"
    finally:
        get_engine.cache_clear()
