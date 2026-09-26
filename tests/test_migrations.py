from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.core.config import PROJECT_ROOT, get_settings
from app.models import Base


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def use_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()


def test_migrations_match_the_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    use_database(monkeypatch, url)

    command.upgrade(alembic_config(), "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert differences == []


def test_migrations_can_be_downgraded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    use_database(monkeypatch, url)

    command.upgrade(alembic_config(), "head")
    command.downgrade(alembic_config(), "base")

    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_migration_renders_valid_postgresql_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline mode (no connection): the SQL is generated with the PostgreSQL dialect."""
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE candidates" in sql
    assert "CREATE TABLE evidence_links" in sql
    assert "exactly_one_target" in sql
    assert "DEFAULT now()" in sql
    assert "SERIAL" in sql or "GENERATED" in sql
