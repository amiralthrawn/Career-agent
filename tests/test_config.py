from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from app.core.config import PROJECT_ROOT, ConfigurationError, Settings


class _EnvOnlySettings(Settings):
    """Ignores any local .env so tests only see the process environment."""

    model_config = SettingsConfigDict(env_file=None)


def make_settings() -> Settings:
    return _EnvOnlySettings()


def test_defaults() -> None:
    settings = make_settings()

    assert settings.app_env == "development"
    assert settings.app_debug is False
    assert settings.database_url is None
    assert settings.cors_origin_list == []


def test_values_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000")

    settings = make_settings()

    assert settings.app_env == "test"
    assert settings.require_database_url().startswith("postgresql+psycopg://")
    assert settings.cors_origin_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_empty_database_url_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")

    settings = make_settings()

    assert settings.database_url is None
    with pytest.raises(ConfigurationError):
        settings.require_database_url()


def test_database_url_is_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:secret@localhost/db")

    assert "secret" not in repr(make_settings())


def test_private_data_path_is_resolved_against_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert make_settings().private_data_path == PROJECT_ROOT / "data" / "private"

    absolute = Path(PROJECT_ROOT.anchor) / "somewhere" / "private"
    monkeypatch.setenv("PRIVATE_DATA_DIR", str(absolute))
    assert make_settings().private_data_path == absolute
