"""Database-level guarantees of profiles, criteria and qualifications."""

import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Float, Numeric, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Base,
    CriterionResult,
    Qualification,
    QualificationReason,
    SearchCriterion,
    SearchProfile,
)
from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    CriterionOutcome,
    EvaluationCode,
    QualificationStatus,
    ReasonCode,
)
from tests import targets_factory as f
from tests.test_migrations import alembic_config

QUALIFICATION_TABLES = (
    "search_profiles",
    "search_criteria",
    "qualifications",
    "criterion_results",
    "qualification_reasons",
)


def make_criterion(session: Session, profile: SearchProfile, **fields: object) -> SearchCriterion:
    values: dict[str, object] = {
        "profile_id": profile.id,
        "dimension": CriterionDimension.SECTOR,
        "operator": CriterionOperator.ANY_OF,
        "match_values": ["software"],
        "level": CriterionLevel.REQUIRED,
    }
    row = SearchCriterion(**{**values, **fields})
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def chain(db_session: Session) -> dict[str, object]:
    candidate = f.candidate(db_session)
    company = f.company(db_session)
    target = f.target(db_session, candidate.id, company.id)
    profile = SearchProfile(candidate_id=candidate.id, name="P", is_active=True)
    db_session.add(profile)
    db_session.flush()
    criterion = make_criterion(db_session, profile)
    qualification = Qualification(
        candidate_id=candidate.id,
        target_id=target.id,
        profile_id=profile.id,
        inputs_fingerprint="0" * 64,
        status=QualificationStatus.CANDIDATE,
    )
    db_session.add(qualification)
    db_session.flush()
    result = CriterionResult(
        qualification_id=qualification.id,
        criterion_id=criterion.id,
        outcome=CriterionOutcome.SATISFIED,
        code=EvaluationCode.MATCH,
        observed="software",
    )
    db_session.add(result)
    db_session.flush()
    reason = QualificationReason(
        qualification_id=qualification.id,
        position=0,
        code=ReasonCode.REQUIRED_SATISFIED,
        criterion_result_id=result.id,
    )
    db_session.add(reason)
    db_session.commit()
    return {
        "candidate": candidate,
        "target": target,
        "profile": profile,
        "criterion": criterion,
        "qualification": qualification,
        "result": result,
        "reason": reason,
    }


# --- A qualification is immutable ------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "field", "value"),
    [
        ("qualification", "status", QualificationStatus.EXCLUDED),
        ("qualification", "inputs_fingerprint", "1" * 64),
        ("result", "outcome", CriterionOutcome.INCOMPATIBLE),
        ("result", "observed", "changed"),
        ("reason", "code", ReasonCode.EXCLUDED_BY_REQUIRED),
    ],
)
def test_qualification_rows_cannot_be_updated_through_the_orm(
    db_session: Session, chain: dict[str, object], key: str, field: str, value: object
) -> None:
    setattr(chain[key], field, value)

    with pytest.raises(RuntimeError, match="immutable"):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("table", ["qualifications", "criterion_results", "qualification_reasons"])
def test_qualification_rows_cannot_be_updated_in_raw_sql_either(
    db_session: Session, chain: dict[str, object], table: str
) -> None:
    column = {"qualifications": "status", "criterion_results": "observed"}.get(table, "position")
    value = {"qualifications": "'excluded'", "criterion_results": "'x'"}.get(table, "9")

    with pytest.raises((DBAPIError, IntegrityError), match="immutable"):
        db_session.execute(text(f"UPDATE {table} SET {column} = {value}"))
    db_session.rollback()


def test_a_new_qualification_is_added_next_to_the_old_one(
    db_session: Session, chain: dict[str, object]
) -> None:
    old = chain["qualification"]
    assert isinstance(old, Qualification)

    db_session.add(
        Qualification(
            candidate_id=old.candidate_id,
            target_id=old.target_id,
            profile_id=old.profile_id,
            inputs_fingerprint="2" * 64,
            status=QualificationStatus.EXCLUDED,
        )
    )
    db_session.commit()

    statuses = [q.status for q in db_session.query(Qualification).order_by(Qualification.id)]
    assert statuses == [QualificationStatus.CANDIDATE, QualificationStatus.EXCLUDED]


def test_a_criterion_used_by_a_result_cannot_be_deleted(
    db_session: Session, chain: dict[str, object]
) -> None:
    """History stays readable: a criterion that explains a stored result cannot vanish."""
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM search_criteria"))
    db_session.rollback()


def test_deleting_a_qualification_cascades_to_its_results_and_reasons(
    db_session: Session, chain: dict[str, object]
) -> None:
    """Only UPDATE is forbidden: deletion stays possible so that cascades work."""
    db_session.execute(text("DELETE FROM qualifications"))
    db_session.commit()

    assert db_session.query(CriterionResult).count() == 0
    assert db_session.query(QualificationReason).count() == 0


# --- A criterion keeps its content -----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dimension", CriterionDimension.ROLE),
        ("operator", CriterionOperator.NONE_OF),
        ("match_values", ["fintech"]),
        ("level", CriterionLevel.PREFERRED),
        ("note", "rewritten"),
        ("origin_ref", "constraint:1"),
    ],
)
def test_criterion_content_cannot_be_edited(
    db_session: Session, chain: dict[str, object], field: str, value: object
) -> None:
    criterion = chain["criterion"]
    assert isinstance(criterion, SearchCriterion)
    setattr(criterion, field, value)

    with pytest.raises(RuntimeError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_criterion_content_cannot_be_edited_in_raw_sql_but_deactivation_can(
    db_session: Session, chain: dict[str, object]
) -> None:
    for column, value in (
        ("level", "'preferred'"),
        ("match_values", "'[\"x\"]'"),
        ("dimension", "'role'"),
    ):
        with pytest.raises((DBAPIError, IntegrityError), match="immutable"):
            db_session.execute(text(f"UPDATE search_criteria SET {column} = {value}"))
        db_session.rollback()

    db_session.execute(
        text("UPDATE search_criteria SET active = 0, deactivated_at = CURRENT_TIMESTAMP")
    )
    db_session.commit()
    criterion = chain["criterion"]
    assert isinstance(criterion, SearchCriterion)
    db_session.refresh(criterion)
    assert criterion.active is False and criterion.match_values == ["software"]


def test_a_criterion_can_be_deactivated_with_a_successor(
    db_session: Session, chain: dict[str, object]
) -> None:
    profile = chain["profile"]
    criterion = chain["criterion"]
    assert isinstance(profile, SearchProfile) and isinstance(criterion, SearchCriterion)
    successor = make_criterion(db_session, profile, match_values=["fintech"])

    criterion.active = False
    criterion.superseded_by_id = successor.id
    db_session.commit()  # only the lifecycle columns changed: allowed

    db_session.refresh(criterion)
    assert criterion.superseded_by_id == successor.id and criterion.match_values == ["software"]


# --- Other integrity rules -------------------------------------------------------------


def test_one_active_profile_per_candidate(db_session: Session, chain: dict[str, object]) -> None:
    candidate = chain["candidate"]
    db_session.add(SearchProfile(candidate_id=candidate.id, name="Other", is_active=True))  # type: ignore[attr-defined]

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(SearchProfile(candidate_id=candidate.id, name="Inactive", is_active=False))  # type: ignore[attr-defined]
    db_session.commit()  # several inactive profiles are fine


def test_a_criterion_is_evaluated_once_per_qualification(
    db_session: Session, chain: dict[str, object]
) -> None:
    db_session.add(
        CriterionResult(
            qualification_id=chain["qualification"].id,  # type: ignore[attr-defined]
            criterion_id=chain["criterion"].id,  # type: ignore[attr-defined]
            outcome=CriterionOutcome.UNKNOWN,
            code=EvaluationCode.DATA_MISSING,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_reason_can_only_reference_an_existing_result(
    db_session: Session, chain: dict[str, object]
) -> None:
    db_session.add(
        QualificationReason(
            qualification_id=chain["qualification"].id,  # type: ignore[attr-defined]
            position=1,
            code=ReasonCode.PREFERRED_SATISFIED,
            criterion_result_id=99999,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- No score in the model -------------------------------------------------------------

FORBIDDEN = ("score", "percent", "pct", "rating", "weight", "points", "ratio", "rank", "grade")


def test_no_qualification_column_is_a_score_or_a_decimal() -> None:
    for name in QUALIFICATION_TABLES:
        table = Base.metadata.tables[name]
        for column in table.columns:
            assert not any(word in column.name.lower() for word in FORBIDDEN), (name, column.name)
            assert not isinstance(column.type, Float | Numeric), (name, column.name)


# --- The migration builds the same protections -----------------------------------------


def test_a_database_built_by_the_migrations_enforces_immutability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "built.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO candidates (first_name, last_name) VALUES ('T', 'C')")
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO companies (name, name_key, contact_research, source_id) "
        "VALUES ('A', 'a', 'not_started', 1)"
    )
    connection.execute(
        "INSERT INTO targets (candidate_id, company_id, status, source_id) VALUES (1, 1, 'new', 1)"
    )
    connection.execute(
        "INSERT INTO search_profiles (candidate_id, name, is_active, origin, unmapped) "
        "VALUES (1, 'P', 1, 'manual', '[]')"
    )
    connection.execute(
        "INSERT INTO search_criteria (profile_id, dimension, operator, match_values, level, origin,"
        " active) VALUES (1, 'sector', 'any_of', '[\"software\"]', 'required', 'manual', 1)"
    )
    connection.execute(
        "INSERT INTO qualifications (candidate_id, target_id, profile_id, method,"
        " inputs_fingerprint, status) VALUES (1, 1, 1, 'deterministic', 'f', 'candidate')"
    )

    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute("UPDATE qualifications SET status = 'excluded'")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute("UPDATE search_criteria SET level = 'preferred'")
    connection.execute("UPDATE search_criteria SET active = 0")  # lifecycle columns stay editable
    connection.execute("DELETE FROM qualifications")  # cascades keep working
    connection.close()


def test_the_migration_renders_the_immutability_triggers_for_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost/unused")
    get_settings.cache_clear()
    output = StringIO()
    config = alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0005:0006", sql=True)

    sql = output.getvalue()
    assert "CREATE OR REPLACE FUNCTION forbid_row_update()" in sql
    for table in ("qualifications", "criterion_results", "qualification_reasons"):
        assert f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}" in sql
    assert "CREATE TRIGGER search_criteria_immutable BEFORE UPDATE OF profile_id, dimension" in sql
    assert "CREATE UNIQUE INDEX uq_search_profiles_active" in sql and "WHERE is_active" in sql


def test_the_migration_adds_and_removes_remote_mode_without_losing_offers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "remote.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO sources (kind, label) VALUES ('manual', 'fixture')")
    connection.execute(
        "INSERT INTO companies (name, name_key, contact_research, source_id) "
        "VALUES ('A', 'a', 'not_started', 1)"
    )
    connection.execute(
        "INSERT INTO opportunities (company_id, title, title_key, status, remote_mode, source_id) "
        "VALUES (1, 'Offer', 'offer', 'unknown', 'remote', 1)"
    )
    connection.commit()
    connection.close()

    command.downgrade(alembic_config(), "0005")

    connection = sqlite3.connect(database)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(opportunities)")]
    assert "remote_mode" not in columns
    assert connection.execute("SELECT title FROM opportunities").fetchall() == [("Offer",)]
    connection.close()
    command.upgrade(alembic_config(), "head")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT title, remote_mode FROM opportunities").fetchall() == [
        ("Offer", None)  # a re-created column is empty: nothing is invented
    ]
    connection.close()


def test_the_migration_renders_the_remote_mode_column_for_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost/unused")
    get_settings.cache_clear()
    up, down = StringIO(), StringIO()
    config_up, config_down = alembic_config(), alembic_config()
    config_up.output_buffer, config_down.output_buffer = up, down

    command.upgrade(config_up, "0005:0006", sql=True)
    command.downgrade(config_down, "0006:0005", sql=True)

    assert "ALTER TABLE opportunities ADD COLUMN remote_mode VARCHAR(32)" in up.getvalue()
    assert "ALTER TABLE opportunities DROP COLUMN remote_mode" in down.getvalue()
