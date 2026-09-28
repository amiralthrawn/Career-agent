"""Integration tests for the seams between modules (fake providers only, no network) - not just
each piece in isolation. Covers, per the operational pipeline:

    Sourcing -> Target -> Qualification -> Requirements -> Brief
    Brief -> FakeLLMClient -> ApplicationDraft
    Draft -> approved -> ApplicationPackage
    ApplicationPackage -> SendBatch -> SendGuard -> DryRunMailProvider -> .eml
    Send result -> ApplicationEvent

and one continuous end-to-end run tying all of them together.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.drafts import get_llm_client
from app.core.config import get_settings
from app.models import Target
from app.models.enums import DraftStatus
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import crit, make_profile
from tests.requirements_factory import extract, offer_target
from tests.sourcing_fakes import FakeWebProvider, hit, install, start


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture(autouse=True)
def with_send_env(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SAFE DEFAULT send mode: every send in this file goes through the dry-run provider
    (a real `.eml` file), never Gmail."""
    monkeypatch.setenv("SEND_MODE", "dry_run")
    monkeypatch.setenv("MAIL_FROM", "sender.fixture@example.invalid")
    get_settings.cache_clear()
    (private_dir / "documents" / "cv.docx").write_bytes(simple_docx(SYNTHETIC_CV_LINES))


def install_llm(client: TestClient, llm: FakeLLMClient | None = None) -> FakeLLMClient:
    fake = llm or FakeLLMClient()
    client.app.dependency_overrides[get_llm_client] = lambda: fake  # type: ignore[attr-defined]
    return fake


def add_accepted_contact(client: TestClient, target_id: int) -> None:
    response = client.post(
        f"/api/targets/{target_id}/contacts",
        json={
            "contact": {
                "full_name": "Jamie Fixture",
                "role_category": "recruiter",
                "channels": [
                    {
                        "kind": "email",
                        "value": "jamie@fixture-corp.example.invalid",
                        "source": {"kind": "manual", "label": "test", "reference": "note"},
                    }
                ],
            }
        },
    )
    assert response.status_code == 200, response.text


def ingest_cv(client: TestClient) -> None:
    """Registers the `.docx` written to `data/private/documents/` by `with_send_env` as a real
    `DocumentIngestion` - writing the file alone is not enough: `ApplicationPackageService`
    looks up this row, never the filesystem directly."""
    response = client.post(
        "/api/candidate/ingestions/cv", json={"source_path": "documents/cv.docx"}
    )
    assert response.status_code in (201, 409), response.text  # 409: already ingested, fine


# === Sourcing -> Target -> Qualification -> Requirements -> Brief ============================


def test_seam_sourcing_to_brief(client: TestClient, db_session: Session) -> None:
    make_profile(client, [crit("keyword", ["data analyst"], "required")])
    install(
        client,
        web=FakeWebProvider([hit(company_name="Fixture Corp", offer_title="Data Analyst Intern")]),
    )

    run = start(client, mode="offers").json()
    assert run["status"] == "completed" and run["targets_created"] == 1

    target = db_session.scalars(select(Target)).one()

    qualification = client.get(f"/api/targets/{target.id}/qualification")
    assert qualification.status_code == 200
    assert qualification.json()["status"] == "candidate"

    # A sourced offer has no description (never derived from the snippet): extraction correctly
    # finds nothing to extract, rather than inventing requirements from a lead's raw text.
    extraction = client.post(f"/api/targets/{target.id}/requirements/extract")
    assert extraction.status_code == 200
    assert extraction.json()["outcome"] == "no_description"

    brief = client.get(f"/api/targets/{target.id}/personalization-brief")
    assert brief.status_code == 200
    assert brief.json()["qualification"]["stale"] is False
    assert brief.json()["strengths"] == []  # nothing invented in the absence of a description


# === Brief -> FakeLLMClient -> ApplicationDraft ==============================================


def test_seam_brief_to_draft(client: TestClient) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = offer_target(client)
    extract(client, target["id"])
    assert client.post(f"/api/targets/{target['id']}/qualify").status_code in (200, 201)

    install_llm(client)
    generated = client.post(f"/api/targets/{target['id']}/drafts", json={})

    assert generated.status_code == 201
    draft = generated.json()
    assert draft["status"] == "proposed"
    assert draft["model"] == "fake-llm-v1"
    assert draft["body"]  # the FakeLLMClient's deterministic, context-only body


# === Draft -> approved -> ApplicationPackage (LLM configured: prepare() drafts internally) =====


def test_seam_draft_to_package_validation(client: TestClient) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = offer_target(client)
    extract(client, target["id"])
    assert client.post(f"/api/targets/{target['id']}/qualify").status_code in (200, 201)
    install_llm(client)

    prepared = client.post(f"/api/applications/{target['id']}/prepare", json={})
    assert prepared.status_code == 201
    package = prepared.json()
    assert package["status"] == "pending_validation" and package["draft_id"] is not None

    approved = client.post(f"/api/applications/{package['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"


# === ApplicationPackage -> SendBatch -> SendGuard -> DryRunMailProvider -> .eml ===============


def test_seam_package_to_dry_run_eml(client: TestClient, private_dir: Path) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = offer_target(client)
    extract(client, target["id"])
    assert client.post(f"/api/targets/{target['id']}/qualify").status_code in (200, 201)
    add_accepted_contact(client, target["id"])
    ingest_cv(client)
    install_llm(client)

    package = client.post(f"/api/applications/{target['id']}/prepare", json={}).json()
    client.post(f"/api/applications/{package['id']}/approve")

    outbox = private_dir / "outbox"
    assert not outbox.exists() or not list(outbox.glob("*.eml"))

    sent = client.post(f"/api/applications/{package['id']}/send")

    assert sent.status_code == 200
    body = sent.json()
    assert body["status"] == "completed"
    (item,) = body["items"]
    assert item["status"] == "sent"
    eml_files = list(outbox.glob("*.eml"))
    assert len(eml_files) == 1
    raw = eml_files[0].read_text(encoding="utf-8", errors="replace")
    assert "cv.docx" in raw  # the CV from data/private/ was attached


# === Send result -> ApplicationEvent ==========================================================


def test_seam_send_to_tracking_event(client: TestClient) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = offer_target(client)
    extract(client, target["id"])
    assert client.post(f"/api/targets/{target['id']}/qualify").status_code in (200, 201)
    add_accepted_contact(client, target["id"])
    ingest_cv(client)
    install_llm(client)

    package = client.post(f"/api/applications/{target['id']}/prepare", json={}).json()
    client.post(f"/api/applications/{package['id']}/approve")
    sent = client.post(f"/api/applications/{package['id']}/send")
    assert sent.json()["status"] == "completed"

    events = client.get(f"/api/applications/{package['id']}/events").json()
    types = {e["event_type"] for e in events}
    assert {"prepared", "approved", "sent"} <= types
    sent_event = next(e for e in events if e["event_type"] == "sent")
    assert sent_event["origin"] == "system" and sent_event["status"] == "found"


# === Full pipeline, one continuous run ========================================================


def test_full_pipeline_sourcing_to_sent_and_tracked(
    client: TestClient, db_session: Session, private_dir: Path
) -> None:
    """Sourcing -> Target -> Qualification -> Requirements -> Brief -> LLM Draft -> human
    approval -> ApplicationPackage -> SendBatch -> SendGuard -> dry-run Gmail -> ApplicationEvent.
    Fake providers only (Perplexity-shaped web search, FakeLLMClient, dry-run mail): no network.
    """
    # 1-2. SearchProfile + a Perplexity-shaped provider
    make_profile(client, [crit("keyword", ["data analyst"], "required")])
    install(
        client,
        web=FakeWebProvider([hit(company_name="Fixture Corp", offer_title="Data Analyst Intern")]),
    )

    # 3-4. A real Target, already qualified as part of the same sourcing run
    run = start(client, mode="offers").json()
    assert run["status"] == "completed" and run["targets_created"] == 1
    target = db_session.scalars(select(Target)).one()
    assert client.get(f"/api/targets/{target.id}/qualification").json()["status"] == "candidate"

    # 5. Requirements: a sourced lead has no description text (correctly extracts nothing)
    assert (
        client.post(f"/api/targets/{target.id}/requirements/extract").json()["outcome"]
        == "no_description"
    )

    # 6. PersonalizationBrief
    assert client.get(f"/api/targets/{target.id}/personalization-brief").status_code == 200

    add_accepted_contact(client, target.id)
    ingest_cv(client)

    # 7-9. LLM Draft generated as part of preparing the package; 8. human approval
    install_llm(client)
    prepared = client.post(f"/api/applications/{target.id}/prepare", json={})
    assert prepared.status_code == 201
    package = prepared.json()
    assert package["status"] == "pending_validation"
    approved = client.post(f"/api/applications/{package['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    # 10-12. SendBatch -> SendGuard -> dry-run Gmail -> .eml
    sent = client.post(f"/api/applications/{package['id']}/send")
    assert sent.status_code == 200 and sent.json()["status"] == "completed"
    (item,) = sent.json()["items"]
    assert item["status"] == "sent"
    assert len(list((private_dir / "outbox").glob("*.eml"))) == 1

    # 13. ApplicationEvent tracking
    events = client.get(f"/api/applications/{package['id']}/events").json()
    assert {"prepared", "approved", "sent"} <= {e["event_type"] for e in events}

    # And the draft that made this all possible is still there, approved-adjacent (proposed,
    # since the package's own approval - not the standalone draft route - was used here).
    draft = client.get(f"/api/drafts/{package['draft_id']}")
    assert draft.status_code == 200 and draft.json()["status"] == DraftStatus.PROPOSED.value
