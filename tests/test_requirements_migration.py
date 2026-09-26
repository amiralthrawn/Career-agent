"""Migration 0007 (requirements and matches): what a database BUILT BY THE MIGRATIONS enforces."""

import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic import command

from tests.test_migrations import alembic_config, use_database

REQUIREMENT = (
    "INSERT INTO target_requirements (candidate_id, target_id, kind, key, label, importance,"
    " origin, source_field, source_id, source_hash, excerpt, active)"
    " VALUES (1, 1, 'skill', '{key}', 'Label', 'unspecified', 'offer_text', 'f', 1, 'h',"
    " 'Some exact text', {active})"
)


def build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    use_database(monkeypatch, f"sqlite:///{tmp_path / 'built.db'}")
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(tmp_path / "built.db")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO candidates (first_name, last_name) VALUES ('T', 'C')")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO companies (name, name_key, domain, contact_research, source_id) "
        "VALUES ('A', 'a', 'a.example.invalid', 'not_started', 1)"
    )
    connection.execute(
        "INSERT INTO targets (candidate_id, company_id, opportunity_id, status, source_id) "
        "VALUES (1, 1, NULL, 'new', 1)"
    )
    return connection


def test_a_migrated_database_keeps_requirement_content_immutable_but_allows_deactivation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(REQUIREMENT.format(key="python", active=1))

    for column in ("excerpt", "importance", "key", "source_hash", "origin", "label"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(f"UPDATE target_requirements SET {column} = {column}")
    connection.execute(
        "UPDATE target_requirements SET active = 0, deactivated_at = CURRENT_TIMESTAMP"
    )
    connection.execute("DELETE FROM target_requirements")  # deleting stays possible (cascades)
    connection.close()


def test_a_migrated_database_allows_one_active_requirement_per_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(REQUIREMENT.format(key="python", active=1))

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(REQUIREMENT.format(key="python", active=1))
    connection.execute(REQUIREMENT.format(key="python", active=0))  # an old version is fine
    connection.execute(REQUIREMENT.format(key="sql", active=1))
    connection.close()


def test_a_migrated_database_refuses_an_empty_excerpt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(REQUIREMENT.replace("'Some exact text'", "''").format(key="x", active=1))
    connection.close()


def test_migration_0007_renders_the_guarantees_for_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0006:0007", sql=True)

    sql = output.getvalue()
    assert "CREATE UNIQUE INDEX uq_target_requirements_active ON target_requirements" in sql
    assert "WHERE active" in sql
    assert "ck_target_requirements_excerpt_not_empty" in sql
    assert "ck_requirement_match_facts_exactly_one_fact" in sql
    for table in ("requirement_matches", "requirement_match_facts"):
        assert f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}" in sql
    assert "CREATE TRIGGER target_requirements_immutable BEFORE UPDATE OF candidate_id" in sql
    assert (
        "active"
        not in sql.split("target_requirements_immutable BEFORE UPDATE OF")[1].split(
            " ON target_requirements"
        )[0]
    )  # the versioning columns stay updatable


def test_downgrading_0007_keeps_the_shared_trigger_function_for_0006(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.downgrade(config, "0007:0006", sql=True)

    sql = output.getvalue()
    assert "DROP TABLE target_requirements" in sql
    assert "DROP FUNCTION" not in sql  # forbid_row_update() still serves the 0006 triggers
