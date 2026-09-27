"""Migration 0009 (application drafts): what a database built by the migrations enforces."""

import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic import command

from tests.test_migrations import alembic_config, use_database

DRAFT = (
    "INSERT INTO application_drafts (candidate_id, target_id, kind, status, model, subject,"
    " body, claims, selected_evidence, warnings{extra_cols})"
    " VALUES (1, 1, 'application_email', '{status}', 'fake', 'Subject', 'Body', '[]', '[]', '[]'"
    "{extra_vals})"
)


def build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    use_database(monkeypatch, f"sqlite:///{tmp_path / 'built.db'}")
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(tmp_path / "built.db")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO candidates (first_name, last_name) VALUES ('T', 'C')")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO companies (name, name_key, contact_research, source_id)"
        " VALUES ('A', 'a', 'not_started', 1)"
    )
    connection.execute(
        "INSERT INTO targets (candidate_id, company_id, opportunity_id, status, source_id)"
        " VALUES (1, 1, NULL, 'new', 1)"
    )
    return connection


def test_content_is_immutable_but_status_and_decision_columns_are_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(DRAFT.format(status="proposed", extra_cols="", extra_vals=""))

    for column, value in (
        ("subject", "subject"),
        ("body", "body"),
        ("model", "model"),
        ("claims", "claims"),
        ("kind", "kind"),
        ("candidate_id", "candidate_id"),
    ):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(f"UPDATE application_drafts SET {column} = {value}")

    connection.execute(
        "UPDATE application_drafts SET status = 'approved', decided_at = CURRENT_TIMESTAMP,"
        " decided_by = 'api'"
    )
    connection.execute("DELETE FROM application_drafts")  # deleting stays possible (cascades)
    connection.close()


def test_only_one_pending_draft_per_target_and_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(DRAFT.format(status="proposed", extra_cols="", extra_vals=""))

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(DRAFT.format(status="proposed", extra_cols="", extra_vals=""))
    connection.execute(
        DRAFT.format(status="superseded", extra_cols=", superseded_by_id", extra_vals=", 1")
    )
    connection.close()


def test_a_decision_needs_a_decided_at_and_the_reverse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)

    with pytest.raises(sqlite3.IntegrityError):  # approved without decided_at
        connection.execute(DRAFT.format(status="approved", extra_cols="", extra_vals=""))
    with pytest.raises(sqlite3.IntegrityError):  # decided_at without a decided status
        connection.execute(
            DRAFT.format(
                status="proposed", extra_cols=", decided_at", extra_vals=", CURRENT_TIMESTAMP"
            )
        )
    connection.execute(
        DRAFT.format(status="approved", extra_cols=", decided_at", extra_vals=", CURRENT_TIMESTAMP")
    )
    connection.close()


def test_an_empty_subject_or_body_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO application_drafts (candidate_id, target_id, kind, status, model,"
            " subject, body, claims, selected_evidence, warnings)"
            " VALUES (1, 1, 'application_email', 'proposed', 'fake', '', 'Body', '[]', '[]', '[]')"
        )
    connection.close()


def test_deleting_a_target_or_qualification_does_not_orphan_the_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = build(monkeypatch, tmp_path)
    connection.execute(DRAFT.format(status="proposed", extra_cols="", extra_vals=""))

    connection.execute("DELETE FROM targets")  # cascades to the draft

    assert connection.execute("SELECT COUNT(*) FROM application_drafts").fetchone()[0] == 0
    connection.close()


def test_migration_0009_renders_valid_postgresql_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline mode: the SQL is generated with the PostgreSQL dialect (no PostgreSQL is run)."""
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0008:0009", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE application_drafts" in sql
    assert "ck_application_drafts_decided_at_iff_decided" in sql
    assert "ck_application_drafts_subject_not_empty" in sql
    assert "UNIQUE" in sql or "uq_application_drafts_pending" in sql
    assert "CREATE TRIGGER application_drafts_immutable BEFORE UPDATE OF candidate_id" in sql
    # the mutable columns are excluded from the trigger's scope
    scope = sql.split("application_drafts_immutable BEFORE UPDATE OF")[1].split(
        " ON application_drafts"
    )[0]
    assert "status" not in scope and "decided_at" not in scope and "superseded_by_id" not in scope


def test_downgrading_0009_keeps_the_shared_trigger_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_database(monkeypatch, "postgresql+psycopg://user:pw@localhost/unused")
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.downgrade(config, "0009:0008", sql=True)

    sql = output.getvalue()
    assert "DROP TABLE application_drafts" in sql
    assert "DROP FUNCTION" not in sql  # still used by 0006/0007's own triggers
