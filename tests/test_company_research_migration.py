"""Migration 0010 (company research facts): a database built by the migrations."""

import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic import command

from tests.test_migrations import alembic_config, use_database

FACT = (
    "INSERT INTO company_research_facts (company_id, claim, retrieved_at)"
    " VALUES (1, 'A fintech company.', CURRENT_TIMESTAMP)"
)


def build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    use_database(monkeypatch, f"sqlite:///{tmp_path / 'built.db'}")
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(tmp_path / "built.db")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO companies (name, name_key, contact_research, source_id)"
        " VALUES ('A', 'a', 'not_started', 1)"
    )
    return connection


def test_a_migrated_database_keeps_facts_immutable_but_deletable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(FACT)

    for column, value in (("claim", "claim"), ("source_url", "source_url"), ("company_id", "1")):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(f"UPDATE company_research_facts SET {column} = {value}")

    connection.execute("DELETE FROM company_research_facts")  # deleting stays possible (cascades)
    connection.close()


def test_deleting_a_company_cascades_to_its_research_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(FACT)

    connection.execute("DELETE FROM companies")

    assert connection.execute("SELECT COUNT(*) FROM company_research_facts").fetchone()[0] == 0
    connection.close()


def test_migration_0010_renders_valid_postgresql_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline mode: the SQL is generated with the PostgreSQL dialect (no PostgreSQL is run)."""
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0009:0010", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE company_research_facts" in sql
    assert "REFERENCES companies" in sql
    assert "CREATE TRIGGER company_research_facts_immutable BEFORE UPDATE " in sql
    assert "ON company_research_facts FOR EACH ROW EXECUTE FUNCTION forbid_row_update()" in sql


def test_downgrading_0010_keeps_the_shared_trigger_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.downgrade(config, "0010:0009", sql=True)

    sql = output.getvalue()
    assert "DROP TABLE company_research_facts" in sql
    assert "DROP FUNCTION" not in sql  # still used by earlier triggers
