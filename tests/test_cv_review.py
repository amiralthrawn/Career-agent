"""Local review of CV proposals: readable by the human, invisible to Git, terminal and logs."""

import hashlib
import importlib.util
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PROJECT_ROOT
from app.models import Education, Evidence, EvidenceLink, IngestionProposal, Skill
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx

BASE = "/api/candidate"
CV_PATH = "documents/synthetic-cv.docx"
SENTINELS = ("Fixture University", "Synthetic coursework", "Fixture Corp", "FixtureLang")


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "preview_cv_proposals", PROJECT_ROOT / "scripts" / "preview_cv_proposals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cv_file(private_dir: Path) -> Path:
    path = private_dir / CV_PATH
    path.write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    return path


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_script()

    def forbidden() -> Any:
        raise AssertionError("preview mode must not touch the database")

    monkeypatch.setattr(module, "get_session_factory", forbidden)
    return module


def only_report(private_dir: Path) -> Path:
    (report,) = list((private_dir / "reviews").iterdir())
    return report


# --- Preview (no database) -------------------------------------------------------------


def test_preview_writes_a_report_with_everything_needed_to_review(
    script: ModuleType, cv_file: Path, private_dir: Path
) -> None:
    assert script.main([CV_PATH]) == 0

    html = only_report(private_dir).read_text(encoding="utf-8")
    assert "Fixture University" in html  # structured data
    assert "Synthetic coursework" in html  # source excerpt
    assert "Text in parentheses was not interpreted" in html  # uncertainty
    assert "pending (preview, not stored)" in html  # status
    for kind in ("education", "experience", "project", "skill", "certification", "language"):
        assert f"&middot; {kind}</h2>" in html  # proposal type
    assert "Nothing was stored, accepted or added" in html


def test_preview_shows_date_precision_without_completing_dates(
    script: ModuleType, cv_file: Path, private_dir: Path
) -> None:
    script.main([CV_PATH])

    html = only_report(private_dir).read_text(encoding="utf-8")

    assert "2018 <span" in html and "(year)" in html
    assert "2021-09 <span" in html and "(month)" in html
    assert "2022-06-15 <span" in html and "(day)" in html
    assert "2018-01-01" not in html and "2021-01-01" not in html


def test_terminal_and_logs_show_only_counters_and_the_report_location(
    script: ModuleType,
    cv_file: Path,
    private_dir: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert script.main([CV_PATH]) == 0

    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    for sentinel in SENTINELS:
        assert sentinel not in output
    assert CV_PATH not in output and "synthetic-cv" not in output  # not even the file name
    assert "Proposals:" in captured.out and "reviews/cv-proposals-preview-" in captured.out
    assert str(private_dir) not in output  # no absolute path either


def test_report_is_written_only_inside_the_private_directory(
    script: ModuleType, cv_file: Path, private_dir: Path
) -> None:
    before = {path for path in private_dir.rglob("*") if path.is_file()}

    script.main([CV_PATH])

    created = {path for path in private_dir.rglob("*") if path.is_file()} - before
    assert len(created) == 1
    (report,) = created
    assert report.parent == private_dir / "reviews"
    assert report.name.startswith("cv-proposals-preview-") and report.suffix == ".html"


def test_preview_never_modifies_the_original_document(script: ModuleType, cv_file: Path) -> None:
    before = (cv_file.stat().st_mtime_ns, hashlib.sha256(cv_file.read_bytes()).hexdigest())

    script.main([CV_PATH])
    script.main([CV_PATH])  # idempotent: the same report is simply rewritten

    assert (cv_file.stat().st_mtime_ns, hashlib.sha256(cv_file.read_bytes()).hexdigest()) == before


def test_report_values_are_html_escaped_and_have_no_script(
    script: ModuleType, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(
        simple_docx(["COMPETENCES", "Langages : <script>alert(1)</script>, <b>Bold</b>"])
    )

    script.main(["documents/cv.docx"])

    html = only_report(private_dir).read_text(encoding="utf-8")
    assert "<script" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Content-Security-Policy" in html and 'name="robots"' in html


def test_an_empty_extraction_says_nothing_about_the_candidate(
    script: ModuleType, private_dir: Path
) -> None:
    (private_dir / "documents" / "cv.docx").write_bytes(simple_docx(["Just some prose."]))

    assert script.main(["documents/cv.docx"]) == 0

    html = only_report(private_dir).read_text(encoding="utf-8")
    assert "absence of information is not negative information" in html


@pytest.mark.parametrize(
    "path", ["documents/absent.docx", "../outside.docx", "/abs/path.docx", "documents/notes.txt"]
)
def test_errors_are_generic_and_exit_non_zero(
    script: ModuleType,
    private_dir: Path,
    capsys: pytest.CaptureFixture[str],
    path: str,
) -> None:
    (private_dir / "documents" / "notes.txt").write_text("x")

    assert script.main([path]) == 1

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("Error: ")
    assert not (private_dir / "reviews").exists()  # nothing was written


def test_invalid_document_error_does_not_leak_content(
    script: ModuleType, private_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (private_dir / "documents" / "broken.docx").write_bytes(b"SYNTHETIC-SECRET-MARKER not a zip")

    assert script.main(["documents/broken.docx"]) == 1

    captured = capsys.readouterr()
    assert "SYNTHETIC-SECRET-MARKER" not in captured.out + captured.err
    assert "not a valid .docx" in captured.err


# --- Stored proposals (database read only) ---------------------------------------------


@pytest.fixture
def stored(
    client: TestClient,
    engine: Engine,
    candidate_payload: dict[str, Any],
    cv_file: Path,
    script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    client.post(BASE, json=candidate_payload)
    ingestion = client.post(f"{BASE}/ingestions/cv", json={"source_path": CV_PATH}).json()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(script, "get_session_factory", lambda: factory)
    result: dict[str, Any] = ingestion
    return result


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_stored_proposals_can_be_reviewed_without_changing_anything(
    client: TestClient,
    script: ModuleType,
    stored: dict[str, Any],
    db_session: Session,
    private_dir: Path,
) -> None:
    ingestion = stored
    rejected = ingestion["proposals"][0]
    client.post(f"{BASE}/proposals/{rejected['id']}/reject", json={})
    before = {
        model: count(db_session, model)
        for model in (Skill, Education, Evidence, EvidenceLink, IngestionProposal)
    }

    assert script.main(["--ingestion-id", str(ingestion["id"])]) == 0

    html = only_report(private_dir).read_text(encoding="utf-8")
    assert "Read-only view of stored proposals" in html
    assert "rejected" in html and "pending" in html  # real statuses
    assert "Fixture University" in html
    db_session.expire_all()
    after = {model: count(db_session, model) for model in before}
    assert after == before
    assert after[Skill] == after[Education] == after[Evidence] == after[EvidenceLink] == 0
    statuses = {proposal.status.value for proposal in db_session.scalars(select(IngestionProposal))}
    assert statuses == {"pending", "rejected"}


def test_reviewing_an_unknown_ingestion_fails_cleanly(
    script: ModuleType,
    stored: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert script.main(["--ingestion-id", "999"]) == 1
    assert "not found" in capsys.readouterr().err
