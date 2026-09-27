"""Migration 0008 (sourcing runs, offers research): a database built by the migrations."""

import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic import command

from tests.test_migrations import alembic_config, use_database

RUN = (
    "INSERT INTO search_runs (candidate_id, profile_id, mode, provider, provider_kind, status,"
    " query, max_results, finished_at, errors, sources_consulted)"
    " VALUES (1, 1, 'offers', 'p', 'web_search', '{status}', '{{}}', {max_results},"
    " {finished}, '[]', '[]')"
)
ITEM = (
    "INSERT INTO search_run_items (run_id, position, outcome, reason, fields, target_id)"
    " VALUES (1, {position}, '{outcome}', {reason}, '[]', {target})"
)


def build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    use_database(monkeypatch, f"sqlite:///{tmp_path / 'built.db'}")
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(tmp_path / "built.db")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO candidates (first_name, last_name) VALUES ('T', 'C')")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO search_profiles (candidate_id, name, is_active, origin, unmapped)"
        " VALUES (1, 'P', 1, 'manual', '[]')"
    )
    connection.execute(
        "INSERT INTO companies (name, name_key, contact_research, source_id)"
        " VALUES ('A', 'a', 'not_started', 1)"
    )
    return connection


def test_a_migrated_database_defaults_offers_research_for_existing_style_inserts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)  # the insert above does not name the new column

    row = connection.execute("SELECT offers_research, offers_research_at FROM companies").fetchone()
    assert tuple(row) == ("not_started", None)
    connection.close()


def test_a_migrated_database_ties_the_end_time_to_the_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)

    connection.execute(RUN.format(status="running", max_results=5, finished="NULL"))
    connection.execute(RUN.format(status="failed", max_results=5, finished="CURRENT_TIMESTAMP"))
    with pytest.raises(sqlite3.IntegrityError):  # running with an end time
        connection.execute(
            RUN.format(status="running", max_results=5, finished="CURRENT_TIMESTAMP")
        )
    with pytest.raises(sqlite3.IntegrityError):  # finished without an end time
        connection.execute(RUN.format(status="completed", max_results=5, finished="NULL"))
    with pytest.raises(sqlite3.IntegrityError):  # a run must ask for at least one result
        connection.execute(RUN.format(status="running", max_results=0, finished="NULL"))
    connection.close()


def test_a_migrated_database_keeps_run_items_coherent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(RUN.format(status="running", max_results=5, finished="NULL"))

    connection.execute(
        ITEM.format(position=0, outcome="rejected", reason="'invalid_field'", target="NULL")
    )
    with pytest.raises(sqlite3.IntegrityError):  # a rejection must say why
        connection.execute(
            ITEM.format(position=1, outcome="rejected", reason="NULL", target="NULL")
        )
    with pytest.raises(sqlite3.IntegrityError):  # a success has no failure reason
        connection.execute(
            ITEM.format(
                position=2, outcome="target_created", reason="'invalid_field'", target="NULL"
            )
        )
    with pytest.raises(sqlite3.IntegrityError):  # one row per position in a run
        connection.execute(
            ITEM.format(position=0, outcome="rejected", reason="'invalid_field'", target="NULL")
        )
    connection.close()


def test_deleting_a_run_deletes_its_items_and_a_profile_with_runs_cascades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(RUN.format(status="running", max_results=5, finished="NULL"))
    connection.execute(
        ITEM.format(position=0, outcome="rejected", reason="'invalid_field'", target="NULL")
    )

    connection.execute("DELETE FROM search_runs")

    assert connection.execute("SELECT COUNT(*) FROM search_run_items").fetchone()[0] == 0
    connection.close()


def test_migration_0008_renders_valid_postgresql_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline mode: the SQL is generated with the PostgreSQL dialect (no PostgreSQL is run)."""
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0007:0008", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE search_runs" in sql and "CREATE TABLE search_run_items" in sql
    assert "ck_search_runs_running_iff_unfinished" in sql
    assert "ck_search_run_items_reason_iff_failed" in sql
    assert "ALTER TABLE companies ADD COLUMN offers_research" in sql
    assert "DEFAULT 'not_started'" in sql
    assert "UNIQUE (run_id, position)" in sql or "uq_search_run_items_run_id_position" in sql


def test_downgrading_0008_removes_only_what_it_added(monkeypatch: pytest.MonkeyPatch) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.downgrade(config, "0008:0007", sql=True)

    sql = output.getvalue()
    assert "DROP TABLE search_run_items" in sql and "DROP TABLE search_runs" in sql
    assert "DROP COLUMN offers_research" in sql
    assert "requirement" not in sql
