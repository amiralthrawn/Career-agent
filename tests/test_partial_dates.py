"""Dates keep the precision of their source: a year is never turned into a full date."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.models import Candidate, Education
from app.models.partial_date import (
    DatePrecision,
    is_before,
    normalize_partial_date,
    precision_of,
)
from app.services import cv_parser
from tests.docx_factory import simple_docx

BASE = "/api/candidate"


# --- Value rules -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "precision"),
    [
        ("2024", DatePrecision.YEAR),
        ("2024-06", DatePrecision.MONTH),
        ("2024-06-15", DatePrecision.DAY),
        (None, None),
        ("", None),
    ],
)
def test_precision_is_read_from_the_value_never_added(
    value: str | None, precision: DatePrecision | None
) -> None:
    assert precision_of(value) is precision


@pytest.mark.parametrize("value", ["2024", "2024-06", "2024-06-15", "2024-02-29"])
def test_valid_partial_dates_are_returned_unchanged(value: str) -> None:
    assert normalize_partial_date(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "24",
        "2024-1",
        "2024-13",
        "2024-00",
        "2024-02-30",
        "2023-02-29",
        "2024-06-15T10:00",
        "1800",
        "juin 2024",
    ],
)
def test_invalid_partial_dates_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_partial_date(value)


def test_comparison_only_uses_the_precision_both_dates_share() -> None:
    assert is_before("2023", "2024-06") is True
    assert is_before("2023-12", "2024") is True
    assert is_before("2024", "2024-06") is False  # overlap: not certainly earlier
    assert is_before("2024-06", "2024") is False
    assert is_before("2024-07-01", "2024-06-30") is False


# --- Persistence -----------------------------------------------------------------------


def test_a_year_is_stored_as_a_year(db_session: Session) -> None:
    candidate = Candidate(first_name="Test", last_name="Candidate-Fixture")
    db_session.add(candidate)
    db_session.commit()
    db_session.add(
        Education(
            candidate_id=candidate.id, institution="Fixture", start_date="2024", end_date="2026-06"
        )
    )
    db_session.commit()

    raw = db_session.execute(text("SELECT start_date, end_date FROM education")).one()

    assert tuple(raw) == ("2024", "2026-06")  # no "-01-01", no invented day


def test_the_database_layer_refuses_invalid_or_fabricated_values(db_session: Session) -> None:
    from datetime import date

    candidate = Candidate(first_name="Test", last_name="Candidate-Fixture")
    db_session.add(candidate)
    db_session.commit()

    db_session.add(
        Education(candidate_id=candidate.id, institution="Fixture", start_date="2024-13")
    )
    with pytest.raises(StatementError, match="month must be"):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        Education(candidate_id=candidate.id, institution="Fixture", start_date=date(2024, 1, 1))
    )
    with pytest.raises(StatementError, match="ISO strings"):
        db_session.commit()


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post(BASE, json=candidate_payload).status_code == 201


def post_education(client: TestClient, **fields: Any) -> Any:
    return client.post(f"{BASE}/education", json={"institution": "Fixture Institute", **fields})


def test_api_keeps_and_reports_each_precision(client: TestClient, with_candidate: None) -> None:
    created = post_education(client, start_date="2018", end_date="2021-06").json()
    other = post_education(client, institution="Other", start_date="2022-09-01").json()
    unknown = post_education(client, institution="Third").json()

    assert (created["start_date"], created["start_date_precision"]) == ("2018", "year")
    assert (created["end_date"], created["end_date_precision"]) == ("2021-06", "month")
    assert (other["start_date"], other["start_date_precision"]) == ("2022-09-01", "day")
    assert unknown["start_date"] is None and unknown["start_date_precision"] is None
    listed = {item["id"]: item for item in client.get(f"{BASE}/education").json()}
    assert listed[created["id"]]["start_date"] == "2018"


def test_api_certification_dates_report_their_precision(
    client: TestClient, with_candidate: None
) -> None:
    created = client.post(
        f"{BASE}/certifications", json={"name": "Fixture Cert", "issue_date": "2023"}
    ).json()

    assert created["issue_date"] == "2023" and created["issue_date_precision"] == "year"
    assert created["expiration_date"] is None and created["expiration_date_precision"] is None


@pytest.mark.parametrize("value", ["24", "2024-1", "2024-13", "2024-02-30", "2024-06-15T10:00:00"])
def test_api_rejects_malformed_dates(client: TestClient, with_candidate: None, value: str) -> None:
    assert post_education(client, start_date=value).status_code == 422


def test_api_end_before_start_is_rejected_on_the_shared_precision(
    client: TestClient, with_candidate: None
) -> None:
    assert post_education(client, start_date="2024-06", end_date="2023").status_code == 422
    assert (
        post_education(client, institution="B", start_date="2024-06", end_date="2024").status_code
        == 201
    )


# --- Parser: never more precise than the CV --------------------------------------------


def parsed_dates(period_text: str) -> tuple[str | None, str | None, list[str]]:
    (draft,) = cv_parser.parse_cv(
        f"FORMATION\nMaster Fixture, Test Institute of Fixtures {period_text}\n"
    ).drafts
    return draft.data["start_date"], draft.data["end_date"], draft.uncertainties


@pytest.mark.parametrize(
    ("cv_text", "start", "end"),
    [
        ("2018 - 2021", "2018", "2021"),
        ("juin 2018 - sept. 2021", "2018-06", "2021-09"),
        ("06/2018 - 09/2021", "2018-06", "2021-09"),
        ("15/06/2018 - 30/09/2021", "2018-06-15", "2021-09-30"),
        ("2018 - juin 2021", "2018", "2021-06"),
        ("2018 - Present", "2018", None),
    ],
)
def test_parser_output_has_exactly_the_precision_of_the_cv(
    cv_text: str, start: str, end: str | None
) -> None:
    result_start, result_end, uncertainties = parsed_dates(cv_text)

    assert (result_start, result_end) == (start, end)
    assert uncertainties == []


def test_parser_never_completes_a_year_with_a_month_or_day() -> None:
    for cv_text in ("2018 - 2021", "2024", "2019 - Present"):
        for value in parsed_dates(cv_text)[:2]:
            assert value is None or len(value) == 4, cv_text


def test_ambiguous_numeric_dates_are_flagged() -> None:
    start, _, uncertainties = parsed_dates("03/04/2024 - 15/09/2024")

    assert start == "2024-04-03"
    assert cv_parser.DATE_ORDER_ASSUMED in uncertainties


def test_impossible_dates_are_dropped_and_flagged_not_repaired() -> None:
    start, end, uncertainties = parsed_dates("31/02/2024 - 2025")

    assert start is None and end == "2025"
    assert cv_parser.INVALID_DATE in uncertainties


# --- Workflow: precision survives ingestion and acceptance -----------------------------


def test_year_only_dates_survive_ingestion_and_acceptance(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["FORMATION", "Master Fixture, Test Institute of Fixtures 2018 - 2021"])
    )
    (proposal,) = client.post(
        f"{BASE}/ingestions/cv", json={"source_path": "documents/cv.docx"}
    ).json()["proposals"]
    assert (proposal["data"]["start_date"], proposal["data"]["end_date"]) == ("2018", "2021")
    assert proposal["uncertainties"] == []

    assert client.post(f"{BASE}/proposals/{proposal['id']}/accept", json={}).status_code == 200

    (education,) = client.get(f"{BASE}/education").json()
    assert (education["start_date"], education["start_date_precision"]) == ("2018", "year")
    assert (education["end_date"], education["end_date_precision"]) == ("2021", "year")


def test_corrections_keep_the_precision_given_by_the_human(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["EXPERIENCES", "Fixture Developer - Example Labs", "* Synthetic task"])
    )
    (proposal,) = client.post(
        f"{BASE}/ingestions/cv", json={"source_path": "documents/cv.docx"}
    ).json()["proposals"]
    assert proposal["data"]["start_date"] is None  # nothing stated, nothing invented

    accepted = client.post(
        f"{BASE}/proposals/{proposal['id']}/accept", json={"corrections": {"start_date": "2023-03"}}
    )

    assert accepted.status_code == 200
    (experience,) = client.get(f"{BASE}/experiences").json()
    assert (experience["start_date"], experience["start_date_precision"]) == ("2023-03", "month")
    assert experience["end_date"] is None
