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


def test_migration_0005_renders_the_target_constraints_for_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0004:0005", sql=True)

    sql = output.getvalue()
    assert "CREATE UNIQUE INDEX uq_targets_spontaneous ON targets (candidate_id, company_id)" in sql
    assert "WHERE opportunity_id IS NULL" in sql
    assert "CREATE UNIQUE INDEX uq_targets_offer ON targets (candidate_id, opportunity_id)" in sql
    assert (
        "FOREIGN KEY(opportunity_id, company_id) REFERENCES opportunities (id, company_id)" in sql
    )
    assert "FOREIGN KEY(contact_id, company_id) REFERENCES contacts (id, company_id)" in sql
    assert "CHECK (full_name IS NOT NULL OR is_generic)" in sql
    assert "ck_sources_external_needs_locator" in sql


def test_a_database_built_by_the_migrations_enforces_the_target_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sqlite3

    url = f"sqlite:///{tmp_path / 'built.db'}"
    use_database(monkeypatch, url)
    command.upgrade(alembic_config(), "head")

    connection = sqlite3.connect(tmp_path / "built.db")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO candidates (first_name, last_name) VALUES ('T', 'C')")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    for name, domain in (("A", "a.example.invalid"), ("B", "b.example.invalid")):
        connection.execute(
            "INSERT INTO companies (name, name_key, domain, contact_research, source_id) "
            f"VALUES ('{name}', '{name.lower()}', '{domain}', 'not_started', 1)"
        )
    connection.execute(
        "INSERT INTO opportunities (company_id, title, title_key, status, source_id) "
        "VALUES (2, 'Offer of B', 'offer of b', 'unknown', 1)"
    )
    spontaneous = (
        "INSERT INTO targets (candidate_id, company_id, opportunity_id, status, source_id) "
        "VALUES (1, {company}, {offer}, 'new', 1)"
    )
    connection.execute(spontaneous.format(company=1, offer="NULL"))

    with pytest.raises(sqlite3.IntegrityError):  # a second spontaneous target for company A
        connection.execute(spontaneous.format(company=1, offer="NULL"))
    with pytest.raises(sqlite3.IntegrityError):  # an offer of company B on a target of company A
        connection.execute(spontaneous.format(company=1, offer=1))
    connection.execute(spontaneous.format(company=2, offer=1))  # the right pairing is accepted
    connection.close()
