"""End-to-end tests of the CV ingestion workflow, on synthetic .docx files only."""

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Education,
    Evidence,
    EvidenceLink,
    Experience,
    IngestionProposal,
    Language,
    Project,
    Skill,
)
from app.models.enums import ProposalStatus
from app.repositories.ingestion import claim_pending
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx

BASE = "/api/candidate"
CV_PATH = "documents/synthetic-cv.docx"
SECRET = "SYNTHETIC-SECRET-MARKER"


@pytest.fixture
def cv_file(private_dir: Path) -> Path:
    path = private_dir / CV_PATH
    path.write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    return path


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post(BASE, json=candidate_payload).status_code == 201


def ingest(client: TestClient, path: str = CV_PATH) -> Any:
    return client.post(f"{BASE}/ingestions/cv", json={"source_path": path})


def proposals_of(client: TestClient, **params: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = client.get(f"{BASE}/proposals", params=params).json()
    return result


def find(client: TestClient, kind: str, **expected: Any) -> dict[str, Any]:
    for proposal in proposals_of(client, kind=kind):
        if all(proposal["data"].get(key) == value for key, value in expected.items()):
            return proposal
    raise AssertionError(f"no {kind} proposal with {expected}")


def accept(client: TestClient, proposal: dict[str, Any], **body: Any) -> Any:
    return client.post(f"{BASE}/proposals/{proposal['id']}/accept", json=body)


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- Ingestion creates proposals, nothing else ----------------------------------------


def test_ingestion_creates_only_pending_proposals(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    response = ingest(client)

    assert response.status_code == 201
    body = response.json()
    assert body["parser_version"].startswith("cv-")
    assert body["sha256"] == hashlib.sha256(cv_file.read_bytes()).hexdigest()
    assert body["proposals"] and {p["status"] for p in body["proposals"]} == {"pending"}
    assert body["evidence_id"] is None
    # Not a single fact or evidence exists yet: pending proposals are not facts.
    for model in (Skill, Education, Experience, Project, Language, Evidence, EvidenceLink):
        assert count(db_session, model) == 0
    for path in ("skills", "education", "experiences", "projects", "languages", "evidence"):
        assert client.get(f"{BASE}/{path}").json() == []


def test_every_proposal_keeps_its_source_excerpt_and_uncertainties(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    proposals = ingest(client).json()["proposals"]

    assert all(p["source_excerpt"].strip() for p in proposals)
    assert all(isinstance(p["uncertainties"], list) for p in proposals)
    python = next(p for p in proposals if p["data"].get("name") == "Python")
    assert "Python" in python["source_excerpt"]
    assert python["data"]["level"] is None and python["uncertainties"]


def test_ingestion_requires_a_candidate(client: TestClient, cv_file: Path) -> None:
    assert ingest(client).status_code == 404


def test_the_same_document_cannot_be_ingested_twice(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    assert ingest(client).status_code == 201
    assert ingest(client).status_code == 409
    assert len(client.get(f"{BASE}/ingestions").json()) == 1


def test_a_changed_document_is_a_new_ingestion(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    assert ingest(client).status_code == 201
    cv_file.write_bytes(simple_docx([*SYNTHETIC_CV_LINES, "Allemand : A2"]))

    second = ingest(client)

    assert second.status_code == 201
    assert len(client.get(f"{BASE}/ingestions").json()) == 2


def test_ingestion_can_be_read_back(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    created = ingest(client).json()

    fetched = client.get(f"{BASE}/ingestions/{created['id']}").json()

    assert fetched["id"] == created["id"] and len(fetched["proposals"]) == len(created["proposals"])
    assert client.get(f"{BASE}/ingestions/999").status_code == 404
    assert client.get(f"{BASE}/proposals/999").status_code == 404


# --- File safety -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/cv.docx",
        "C:\\Users\\someone\\cv.docx",
        "../cv.docx",
        "~/cv.docx",
        "https://x.invalid/a",
    ],
)
def test_paths_outside_the_private_directory_are_rejected(
    client: TestClient, with_candidate: None, private_dir: Path, path: str
) -> None:
    assert ingest(client, path).status_code == 422


def test_links_escaping_the_private_directory_are_rejected(
    client: TestClient, with_candidate: None, private_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cv.docx").write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    link = private_dir / "documents" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # no symlink privilege on Windows: a directory junction needs none
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if junction.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available")

    response = ingest(client, "documents/escape/cv.docx")

    assert response.status_code == 422
    assert "private data directory" in response.json()["detail"]
    assert client.get(f"{BASE}/ingestions").json() == []


def test_a_proposal_can_be_claimed_only_once(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    proposal_id = ingest(client).json()["proposals"][0]["id"]

    assert claim_pending(db_session, proposal_id, ProposalStatus.ACCEPTED) is True
    assert claim_pending(db_session, proposal_id, ProposalStatus.REJECTED) is False
    db_session.rollback()
    assert claim_pending(db_session, proposal_id, ProposalStatus.REJECTED) is True


def test_missing_and_unsupported_files(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "notes.txt").write_text("x")

    assert ingest(client, "documents/absent.docx").status_code == 404
    assert ingest(client, "documents/notes.txt").status_code == 422
    assert ingest(client, "documents").status_code == 422


def test_invalid_docx_is_rejected_without_leaking_content(
    client: TestClient,
    with_candidate: None,
    private_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (private_dir / "documents" / "broken.docx").write_bytes(f"{SECRET} not a zip".encode())
    (private_dir / "documents" / "empty.docx").write_bytes(simple_docx(["", ""]))

    with caplog.at_level(logging.DEBUG):
        broken = ingest(client, "documents/broken.docx")
        empty = ingest(client, "documents/empty.docx")

    assert broken.status_code == empty.status_code == 422
    assert SECRET not in broken.text and SECRET not in caplog.text
    assert client.get(f"{BASE}/ingestions").json() == []


def test_oversized_file_is_rejected(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "huge.docx").write_bytes(b"0" * (5 * 1024 * 1024 + 1))

    response = ingest(client, "documents/huge.docx")

    assert response.status_code == 422 and "too large" in response.json()["detail"]


def test_document_text_never_reaches_logs_or_error_messages(
    client: TestClient,
    with_candidate: None,
    private_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["COMPETENCES", f"{SECRET}, Python", "FORMATION", f"{SECRET} Institute"])
    )

    with caplog.at_level(logging.DEBUG):
        response = ingest(client, "documents/cv.docx")
        proposal = response.json()["proposals"][0]
        client.post(f"{BASE}/proposals/{proposal['id']}/accept", json={"corrections": {"x": 1}})

    assert SECRET not in caplog.text


def test_original_document_is_never_modified(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    before = (cv_file.stat().st_mtime_ns, hashlib.sha256(cv_file.read_bytes()).hexdigest())

    proposals = ingest(client).json()["proposals"]
    accept(client, proposals[0], acknowledge_uncertainties=True)
    client.post(f"{BASE}/proposals/{proposals[1]['id']}/reject", json={})

    assert cv_file.exists()
    assert (cv_file.stat().st_mtime_ns, hashlib.sha256(cv_file.read_bytes()).hexdigest()) == before


# --- Acceptance ------------------------------------------------------------------------


def test_acceptance_creates_fact_evidence_and_link(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    ingestion = ingest(client).json()
    sql = next(p for p in ingestion["proposals"] if p["data"].get("name") == "SQL")

    response = accept(client, sql)

    assert response.status_code == 200
    decided = response.json()
    assert decided["status"] == "accepted" and decided["decided_at"]
    assert decided["linked_existing"] is False
    (skill,) = client.get(f"{BASE}/skills").json()
    assert skill["id"] == decided["resulting_fact_id"] and skill["name"] == "SQL"
    assert skill["level"] is None
    (evidence,) = client.get(f"{BASE}/evidence").json()
    assert evidence["source_type"] == "cv"
    assert evidence["source_uri"] == CV_PATH
    assert evidence["source_metadata"]["sha256"] == ingestion["sha256"]
    (link,) = evidence["links"]
    assert (link["target_type"], link["target_id"]) == ("skill", skill["id"])
    assert link["note"] == sql["source_excerpt"]
    assert skill["evidence_ids"] == [evidence["id"]]
    assert (
        client.get(f"{BASE}/ingestions/{ingestion['id']}").json()["evidence_id"] == evidence["id"]
    )


def test_cv_facts_are_known_but_never_verified(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    for proposal in ingest(client).json()["proposals"]:
        assert accept(client, proposal, acknowledge_uncertainties=True).status_code == 200

    evidence = client.get(f"{BASE}/evidence").json()
    assert len(evidence) == 1  # one Evidence per document, shared by all its facts
    assert evidence[0]["verified"] is False
    assert evidence[0]["confidence"] == "medium"
    states = {
        fact["state"]
        for path in (
            "skills",
            "education",
            "experiences",
            "projects",
            "languages",
            "certifications",
        )
        for fact in client.get(f"{BASE}/{path}").json()
    }
    assert states == {"known"}
    assert len(evidence[0]["links"]) == len(proposals_of(client, status="accepted"))


def test_uncertain_proposals_need_an_explicit_acknowledgement(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    ingestion = ingest(client).json()
    python = next(p for p in ingestion["proposals"] if p["data"].get("name") == "Python")
    assert python["uncertainties"]

    refused = accept(client, python)

    assert refused.status_code == 409
    assert client.get(f"{BASE}/skills").json() == []
    assert proposals_of(client, status="pending") and not proposals_of(client, status="accepted")
    assert accept(client, python, acknowledge_uncertainties=True).status_code == 200


def test_corrections_complete_a_proposal_and_are_recorded(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["EXPERIENCES", "2023 - 2024", "* Synthetic task"])
    )
    (proposal,) = ingest(client, "documents/cv.docx").json()["proposals"]

    incomplete = accept(client, proposal, acknowledge_uncertainties=True)
    assert incomplete.status_code == 422 and "company" in incomplete.json()["detail"]
    assert client.get(f"{BASE}/experiences").json() == []
    assert proposals_of(client)[0]["status"] == "pending"  # nothing half-done

    fixed = accept(
        client,
        proposal,
        acknowledge_uncertainties=True,
        corrections={
            "company": "Fixture Corp",
            "title": "Fixture Role",
            "start_date": "2023-03-01",
        },
    )

    assert fixed.status_code == 200
    assert fixed.json()["data"]["company"] is None  # the extraction is kept as extracted
    assert fixed.json()["reviewed_data"]["company"] == "Fixture Corp"
    (experience,) = client.get(f"{BASE}/experiences").json()
    assert experience["start_date"] == "2023-03-01" and experience["title"] == "Fixture Role"


def test_unknown_correction_fields_are_rejected(
    client: TestClient, with_candidate: None, cv_file: Path
) -> None:
    sql = next(p for p in ingest(client).json()["proposals"] if p["data"].get("name") == "SQL")

    response = accept(client, sql, corrections={"nmae": "typo"})

    assert response.status_code == 422 and "nmae" in response.json()["detail"]
    assert client.get(f"{BASE}/skills").json() == []


def test_double_acceptance_is_refused(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    sql = next(p for p in ingest(client).json()["proposals"] if p["data"].get("name") == "SQL")

    assert accept(client, sql).status_code == 200
    assert accept(client, sql).status_code == 409

    assert count(db_session, Skill) == 1 and count(db_session, EvidenceLink) == 1


def test_existing_equivalent_fact_is_reused_not_duplicated(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    manual = client.post(f"{BASE}/skills", json={"name": "sql"}).json()
    assert manual["state"] == "unknown"
    sql = next(p for p in ingest(client).json()["proposals"] if p["data"].get("name") == "SQL")

    decided = accept(client, sql).json()

    assert decided["linked_existing"] is True and decided["resulting_fact_id"] == manual["id"]
    (skill,) = client.get(f"{BASE}/skills").json()
    assert skill["id"] == manual["id"] and skill["state"] == "known"
    assert count(db_session, Skill) == 1 and count(db_session, EvidenceLink) == 1


# --- Rejection -------------------------------------------------------------------------


def test_rejection_creates_nothing_and_is_final(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    ingestion = ingest(client).json()
    sql = next(p for p in ingestion["proposals"] if p["data"].get("name") == "SQL")

    rejected = client.post(f"{BASE}/proposals/{sql['id']}/reject", json={"note": "not mine"})

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected" and rejected.json()["review_note"] == "not mine"
    assert rejected.json()["resulting_fact_id"] is None
    for model in (Skill, Evidence, EvidenceLink):
        assert count(db_session, model) == 0
    assert client.get(f"{BASE}/skills").json() == []
    assert accept(client, sql, acknowledge_uncertainties=True).status_code == 409
    assert client.post(f"{BASE}/proposals/{sql['id']}/reject", json={}).status_code == 409
    assert client.get(f"{BASE}/skills").json() == []
    assert client.get(f"{BASE}/proposals/{sql['id']}").json()["status"] == "rejected"


def test_pending_and_rejected_proposals_are_never_facts(
    client: TestClient, with_candidate: None, cv_file: Path, db_session: Session
) -> None:
    proposals = ingest(client).json()["proposals"]
    for proposal in proposals[::2]:
        client.post(f"{BASE}/proposals/{proposal['id']}/reject", json={})

    assert count(db_session, IngestionProposal) == len(proposals)
    assert not proposals_of(client, status="accepted")
    for model in (Skill, Education, Experience, Project, Language, Evidence, EvidenceLink):
        assert count(db_session, model) == 0
    statuses = {p.status for p in db_session.scalars(select(IngestionProposal))}
    assert statuses == {ProposalStatus.PENDING, ProposalStatus.REJECTED}


def test_proposals_can_be_filtered(client: TestClient, with_candidate: None, cv_file: Path) -> None:
    ingestion = ingest(client).json()
    first = ingestion["proposals"][0]
    client.post(f"{BASE}/proposals/{first['id']}/reject", json={})

    assert [p["id"] for p in proposals_of(client, status="rejected")] == [first["id"]]
    assert all(p["kind"] == "skill" for p in proposals_of(client, kind="skill"))
    assert len(proposals_of(client, ingestion_id=str(ingestion["id"]))) == len(
        ingestion["proposals"]
    )
    assert client.get(f"{BASE}/proposals", params={"status": "nope"}).status_code == 422


def test_absence_in_the_cv_is_not_a_negative_fact(
    client: TestClient, with_candidate: None, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["FORMATION", "Master Fixture, Test Institute of Fixtures"])
    )

    ingest(client, "documents/cv.docx")

    assert not proposals_of(client, kind="skill")
    assert client.get(f"{BASE}/skills").json() == []  # unknown / not known, never "no skills"
