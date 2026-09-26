"""Audit trail: append-only, whitelisted, and free of secrets and mail content."""

import logging
import sqlite3
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_settings
from app.main import create_app
from app.models import AuditEvent
from app.models.audit import AuditEventType
from app.repositories import audit as audit_repository
from app.services.audit import ALLOWED_DETAIL_KEYS, AuditLog, AuditRejectedError


@pytest.fixture
def audit(db_session: Session) -> AuditLog:
    return AuditLog(db_session)


# --- Recording -------------------------------------------------------------------------


def test_an_event_is_recorded_with_time_type_actor_and_details(audit: AuditLog) -> None:
    event = audit.record(
        AuditEventType.SEND_BLOCKED,
        actor="cli",
        subject="secret:openrouter_api_key",
        details={"reason": "send_disabled", "mode": "disabled", "attachments": 0},
    )

    assert event.id and event.occurred_at is not None
    assert event.event_type is AuditEventType.SEND_BLOCKED and event.actor == "cli"
    assert event.details == {"reason": "send_disabled", "mode": "disabled", "attachments": 0}


def test_only_whitelisted_keys_are_accepted(audit: AuditLog) -> None:
    for key in ("body", "subject", "token", "recipient", "email", "filename", "content", "secret"):
        with pytest.raises(AuditRejectedError, match="not allowed"):
            audit.record(AuditEventType.SEND_BLOCKED, actor="system", details={key: "x"})

    assert ALLOWED_DETAIL_KEYS == {
        "reason",
        "mode",
        "recipient_domain",
        "attachments",
        "bytes",
        "name",
        "app_version",
        "rows",
        "created",
        "matched",
        "rejected",
    }


@pytest.mark.parametrize(
    "value",
    ["someone@example.invalid", "line\nbreak", "carriage\rreturn", "x" * 65],
)
def test_values_that_look_like_content_are_refused(audit: AuditLog, value: str) -> None:
    with pytest.raises(AuditRejectedError):
        audit.record(AuditEventType.SEND_BLOCKED, actor="system", details={"reason": value})


@pytest.mark.parametrize("value", [["a"], {"a": 1}, 1.5, b"bytes"])
def test_non_scalar_values_are_refused(audit: AuditLog, value: Any) -> None:
    with pytest.raises(AuditRejectedError):
        audit.record(AuditEventType.SEND_BLOCKED, actor="system", details={"reason": value})


def test_unknown_actor_and_address_like_subject_are_refused(audit: AuditLog) -> None:
    with pytest.raises(AuditRejectedError):
        audit.record(AuditEventType.APP_STARTED, actor="somebody")
    with pytest.raises(AuditRejectedError):
        audit.record(AuditEventType.APP_STARTED, actor="system", subject="me@example.invalid")


def test_event_types_are_a_closed_set(audit: AuditLog) -> None:
    with pytest.raises(Exception):  # noqa: B017 - either a TypeError or a validation error
        audit.record("free.text.event", actor="system")  # type: ignore[arg-type]


def test_recent_events_are_listed_newest_first_and_filterable(audit: AuditLog) -> None:
    audit.record(AuditEventType.APP_STARTED, actor="system")
    audit.record(AuditEventType.SEND_BLOCKED, actor="system")
    audit.record(AuditEventType.APP_STARTED, actor="system")

    recent = audit.recent()
    started = audit.recent(event_type=AuditEventType.APP_STARTED)

    assert [e.id for e in recent] == sorted((e.id for e in recent), reverse=True)
    assert len(recent) == 3 and len(started) == 2
    assert len(audit.recent(limit=1)) == 1


# --- Append-only -----------------------------------------------------------------------


def test_the_repository_offers_no_way_to_change_or_remove_events() -> None:
    public = {name for name in dir(audit_repository) if not name.startswith("_")}

    assert not {n for n in public if "update" in n or "delete" in n or "remove" in n}


def test_orm_updates_and_deletes_are_refused(audit: AuditLog, db_session: Session) -> None:
    event = audit.record(AuditEventType.APP_STARTED, actor="system", details={"mode": "disabled"})

    event.actor = "api"
    with pytest.raises(RuntimeError, match="append-only"):
        db_session.commit()
    db_session.rollback()

    db_session.delete(db_session.get(AuditEvent, event.id))
    with pytest.raises(RuntimeError, match="append-only"):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(AuditEvent, event.id) is not None


def test_bulk_and_raw_sql_changes_are_refused_by_the_database(
    audit: AuditLog, db_session: Session
) -> None:
    event = audit.record(AuditEventType.APP_STARTED, actor="system")

    for statement in (
        update(AuditEvent).values(actor="api"),
        delete(AuditEvent),
        text("UPDATE audit_events SET actor = 'api'"),
        text("DELETE FROM audit_events"),
    ):
        with pytest.raises((DBAPIError, OperationalError), match="append-only"):
            db_session.execute(statement)
        db_session.rollback()

    assert [e.id for e in db_session.scalars(select(AuditEvent))] == [event.id]


def upgrade_to_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")
    return database


def test_the_migration_installs_the_same_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = upgrade_to_head(tmp_path, monkeypatch)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO audit_events (event_type, actor, details) VALUES ('app.started','system','{}')"
    )
    connection.commit()

    for statement in ("UPDATE audit_events SET actor='api'", "DELETE FROM audit_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(statement)
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
    connection.close()


def test_the_migration_renders_postgresql_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost/unused")
    get_settings.cache_clear()
    output = StringIO()
    config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    command.upgrade(config, "0003:0004", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE audit_events" in sql
    assert "BEFORE UPDATE OR DELETE ON audit_events" in sql
    assert "BEFORE TRUNCATE ON audit_events" in sql
    assert "audit_events is append-only" in sql


# --- Startup event ---------------------------------------------------------------------


def test_startup_is_audited_with_the_send_mode(
    configured_database: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEND_MODE", "dry_run")
    get_settings.cache_clear()

    with TestClient(create_app()):
        pass

    with configured_database() as session:
        (event,) = session.scalars(select(AuditEvent)).all()
    assert event.event_type is AuditEventType.APP_STARTED and event.actor == "system"
    assert event.details == {"mode": "dry_run", "app_version": get_settings().app_version}


def test_startup_without_a_database_is_simply_not_audited() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


def test_audit_failure_at_startup_is_tolerated_only_when_sending_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.core.database import get_engine, get_session_factory

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")  # no tables
    for cache in (get_settings, get_engine, get_session_factory):
        cache.cache_clear()

    with caplog.at_level(logging.WARNING, logger="career_agent"):
        with TestClient(create_app()) as client:  # send_mode disabled: starts anyway
            assert client.get("/health").status_code == 200
    assert "Startup audit event could not be recorded" in caplog.text

    monkeypatch.setenv("SEND_MODE", "dry_run")
    get_settings.cache_clear()
    with pytest.raises(OperationalError):  # sending enabled: no audit, no start
        with TestClient(create_app()):
            pass
    get_engine.cache_clear()
    get_session_factory.cache_clear()
