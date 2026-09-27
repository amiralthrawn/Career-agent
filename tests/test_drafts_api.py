"""Application drafts end to end (step 4): generation, truthfulness, human-in-the-loop, security.

The LLM is a generator, never a source of truth: `claims`, `selected_evidence` and `warnings` are
built by the application from the PersonalizationBrief, never parsed out of the model's own text.
Only `FakeLLMClient` is used here: no test in this file, or reachable from it, contacts OpenRouter.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.drafts import get_llm_client
from app.core.secrets import OPENROUTER_API_KEY
from app.integrations.llm.openrouter import OpenRouterClient
from app.integrations.llm.ports import LLMError
from app.models import (
    ApplicationDraft,
    AuditEvent,
    Certification,
    Education,
    Experience,
    Language,
    Project,
    Skill,
)
from app.models.audit import AuditEventType
from app.models.enums import LLMErrorCode
from tests.llm_fakes import FakeLLMClient, echo_context, forbidden_mention
from tests.qualification_factory import add_target, qualify
from tests.requirements_factory import (
    active_profile,
    add_experience,
    add_skill,
    brief,
    manual_body,
    scenario,
    spontaneous_target,
)

FACT_TABLES = (Skill, Project, Experience, Education, Certification, Language)


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def install(client: TestClient, llm: FakeLLMClient | None) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: llm  # type: ignore[attr-defined]


def generate(client: TestClient, target_id: int, **body: Any) -> Any:
    return client.post(f"/api/targets/{target_id}/drafts", json=body)


def fact_counts(session: Session) -> dict[str, int]:
    return {
        t.__name__: session.scalar(select(func.count()).select_from(t)) or 0 for t in FACT_TABLES
    }


# --- Generation --------------------------------------------------------------------------------


def test_generating_a_draft_for_an_offer_target(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())

    response = generate(client, target["id"])

    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "proposed" and draft["kind"] == "application_email"
    assert draft["model"] == "fake-llm-v1"
    assert draft["subject"] == "Application for Data Analyst Intern at Fixture Corp"
    assert "Dear Hiring Team at Fixture Corp" in draft["body"]
    assert draft["usage_prompt_tokens"] == 120 and draft["usage_completion_tokens"] == 80
    assert draft["duration_ms"] == 42
    assert draft["decided_at"] is None and draft["decided_by"] is None


def test_generating_a_draft_for_a_spontaneous_application(client: TestClient) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    add_target(client)  # unrelated target: never touched
    add_skill(client, "Docker", "known")
    qualify(client, target["id"])
    install(client, FakeLLMClient())

    response = generate(client, target["id"])

    assert response.status_code == 201
    draft = response.json()
    assert draft["subject"] == "Spontaneous application to Fixture Corp"
    assert "future opportunities" in draft["body"]


def test_the_context_given_to_the_model_is_built_from_the_brief(client: TestClient) -> None:
    target = scenario(client)
    fake = FakeLLMClient(text=echo_context)
    install(client, fake)

    generate(client, target["id"])

    (sent,) = fake.requests
    assert sent.task == "application_email"
    assert sent.context["company_name"] == "Fixture Corp"
    assert sent.context["offer_title"] == "Data Analyst Intern"
    assert {s["requirement"] for s in sent.context["strengths"]} == {
        "Python",
        "2 years of experience",
    }
    assert {d["requirement"] for d in sent.context["do_not_claim"]} == {"SQL", "Power BI"}
    # nothing beyond the vetted brief travels with the request: no raw offer text, no candidate id
    assert "description_text" not in str(sent.context) and "candidate_id" not in str(sent.context)


def test_the_personalization_brief_and_requirement_matches_are_used(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())

    draft = generate(client, target["id"]).json()

    expected_brief = brief(client, target["id"])
    assert draft["qualification_id"] == expected_brief["qualification"]["id"]
    claimed = {c["requirement"] for c in draft["claims"]}
    assert claimed == {s["requirement"]["label"] for s in expected_brief["strengths"]}
    warned = {w["requirement"] for w in draft["warnings"]}
    assert warned >= {d["requirement"]["label"] for d in expected_brief["do_not_claim"]}


def test_generating_again_supersedes_the_previous_pending_draft(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    first = generate(client, target["id"]).json()

    second = generate(client, target["id"]).json()

    assert second["id"] != first["id"]
    db_session.expire_all()
    old = db_session.get(ApplicationDraft, first["id"])
    assert old is not None and old.status.value == "superseded"
    assert old.superseded_by_id == second["id"] and old.decided_at is None  # not a human decision
    new = db_session.get(ApplicationDraft, second["id"])
    assert new is not None and new.status.value == "proposed"


def test_an_approved_or_rejected_draft_is_never_superseded_by_a_new_generation(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    first = generate(client, target["id"]).json()
    client.post(f"/api/drafts/{first['id']}/approve")

    generate(client, target["id"])

    db_session.expire_all()
    approved = db_session.get(ApplicationDraft, first["id"])
    assert approved is not None
    assert approved.status.value == "approved" and approved.superseded_by_id is None


# --- Truthfulness -------------------------------------------------------------------------------


def test_a_known_skill_can_be_used_as_a_claim(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())

    draft = generate(client, target["id"]).json()

    python_claim = next(c for c in draft["claims"] if c["requirement"] == "Python")
    assert {(f["name"], f["state"]) for f in python_claim["facts"]} >= {("Python", "known")}
    assert "Python" in draft["selected_evidence"][0]["name"] or any(
        f["name"] == "Python" for f in draft["selected_evidence"]
    )


def test_an_absent_skill_can_never_be_invented(client: TestClient) -> None:
    target = scenario(client)  # the Brain has no "Kubernetes" anywhere
    install(client, FakeLLMClient())

    draft = generate(client, target["id"]).json()

    dump = str(draft)
    assert "Kubernetes" not in dump
    assert all(f["name"] != "Kubernetes" for c in draft["claims"] for f in c["facts"])


def test_an_uncertain_or_unknown_skill_is_never_presented_as_certain(client: TestClient) -> None:
    target = scenario(client)  # SQL is a Skill with `uncertain` evidence: weak, not covered

    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()

    assert all(c["requirement"] != "SQL" for c in draft["claims"])
    warning = next(w for w in draft["warnings"] if w["requirement"] == "SQL")
    assert warning["code"] == "weak"
    assert "SQL" not in draft["body"]  # the default fake only echoes claims, never do_not_claim


def test_a_nonexistent_project_can_never_appear_in_the_draft(client: TestClient) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    add_skill(client, "Docker", "known")
    qualify(client, target["id"])
    install(client, FakeLLMClient())

    draft = generate(client, target["id"]).json()

    assert all(f["type"] != "project" for c in draft["claims"] for f in c["facts"])
    assert "Project" not in draft["body"] and "project" not in draft["body"].lower()


def test_no_experience_is_ever_created_by_generating_a_draft(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    before = fact_counts(db_session)
    install(client, FakeLLMClient())

    generate(client, target["id"])
    client.post(f"/api/drafts/{db_session.scalars(select(ApplicationDraft.id)).first()}/approve")

    db_session.expire_all()
    assert fact_counts(db_session) == before  # Candidate/Experience/Project/Skill/... untouched


def test_the_forbidden_mention_guard_flags_a_literal_leak_of_a_do_not_claim_label(
    client: TestClient,
) -> None:
    target = scenario(client)
    install(client, FakeLLMClient(text=forbidden_mention("Power BI")))

    draft = generate(client, target["id"]).json()

    flags = [w for w in draft["warnings"] if w["code"] == "forbidden_mention_detected"]
    assert flags and flags[0]["requirement"] == "Power BI"
    assert draft["status"] == "proposed"  # flagged, not blocked: a human still reviews it


def test_the_forbidden_mention_guard_is_silent_on_a_compliant_draft(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())  # the default builder never mentions do_not_claim items

    draft = generate(client, target["id"]).json()

    assert all(w["code"] != "forbidden_mention_detected" for w in draft["warnings"])


def test_a_year_only_experience_does_not_let_the_draft_assert_a_duration(
    client: TestClient,
) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    # Year-only dates: at most ~2 years, at least ~1 day short of 2 - matching (3b) calls this
    # `unmeasurable`, never `covered` (see test_requirement_matching.py for the same fixture).
    add_experience(client, "Analyst", "2022", "2024", "known")
    client.post(
        f"/api/targets/{target['id']}/requirements",
        json=manual_body(kind="experience", label="x", excerpt="2 years required", value="2 years"),
    )
    qualify(client, target["id"])
    install(client, FakeLLMClient())

    draft = generate(client, target["id"]).json()

    assert all(c["requirement"] != "x" for c in draft["claims"])
    assert any(w["requirement"] == "x" for w in draft["warnings"])


# --- Human-in-the-loop --------------------------------------------------------------------------


def test_a_new_draft_is_always_proposed(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())

    assert generate(client, target["id"]).json()["status"] == "proposed"


def test_a_proposed_draft_can_be_approved(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()

    response = client.post(f"/api/drafts/{draft['id']}/approve")

    assert response.status_code == 200
    approved = response.json()
    assert (
        approved["status"] == "approved"
        and approved["decided_at"]
        and approved["decided_by"] == "api"
    )
    assert client.get(f"/api/drafts/{draft['id']}").json()["status"] == "approved"


def test_a_proposed_draft_can_be_rejected(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()

    response = client.post(f"/api/drafts/{draft['id']}/reject")

    assert response.status_code == 200 and response.json()["status"] == "rejected"


def test_a_decided_draft_cannot_be_decided_again(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()
    client.post(f"/api/drafts/{draft['id']}/approve")

    again = client.post(f"/api/drafts/{draft['id']}/reject")

    assert again.status_code == 422
    assert client.get(f"/api/drafts/{draft['id']}").json()["status"] == "approved"


def test_no_generation_or_decision_ever_sends_mail(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()

    client.post(f"/api/drafts/{draft['id']}/approve")

    send_events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type.in_(
                [
                    AuditEventType.SEND_BLOCKED,
                    AuditEventType.SEND_DRY_RUN,
                    AuditEventType.SEND_APPROVED,
                    AuditEventType.SEND_SENT,
                    AuditEventType.SEND_FAILED,
                ]
            )
        )
    ).all()
    assert send_events == []


def test_there_is_no_send_route_for_a_draft(client: TestClient) -> None:
    for path in ("/api/drafts/1/send", "/api/targets/1/drafts/send"):
        assert client.post(path).status_code in (404, 405)


# --- LLM abstraction ------------------------------------------------------------------------


def test_an_llm_error_is_represented_and_creates_no_draft(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    install(client, FakeLLMClient(raises=LLMError(LLMErrorCode.RATE_LIMITED)))

    response = generate(client, target["id"])

    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(ApplicationDraft)) == 0
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.DRAFT_GENERATED)
    ).one()
    assert event.details == {"reason": "llm_error:rate_limited", "model": "unset"}


def test_without_any_configured_llm_client_generation_is_refused(client: TestClient) -> None:
    target = scenario(client)
    client.app.dependency_overrides.pop(get_llm_client, None)  # type: ignore[attr-defined]

    response = generate(client, target["id"])

    assert response.status_code == 422


def test_a_stale_qualification_refuses_generation(client: TestClient) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    add_skill(client, "Docker", "known")  # the Brain moved on since the qualification

    response = generate(client, target["id"])

    assert response.status_code == 422
    qualify(client, target["id"])
    assert generate(client, target["id"]).status_code == 201


def test_openrouter_is_never_called_when_the_fake_is_injected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: OpenRouterClient, request: Any) -> Any:
        raise AssertionError("OpenRouterClient.generate must never run when a fake is injected")

    monkeypatch.setattr(OpenRouterClient, "generate", boom)
    target = scenario(client)
    install(client, FakeLLMClient())

    assert generate(client, target["id"]).status_code == 201


def test_no_llm_client_is_built_by_default_settings(client: TestClient) -> None:
    """`get_llm_client` returns None unless LLM_ENABLED/OPENROUTER_MODEL/secret are all set."""
    from app.api.drafts import get_llm_client as real_dependency
    from app.core.config import get_settings

    client.app.dependency_overrides.pop(get_llm_client, None)  # type: ignore[attr-defined]
    assert real_dependency(get_settings()) is None


# --- Security -------------------------------------------------------------------------------


def test_the_draft_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/targets/1/drafts"),
        ("get", "/api/drafts/1"),
        ("post", "/api/drafts/1/approve"),
        ("post", "/api/drafts/1/reject"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_no_secret_is_ever_used_by_the_fake_or_appears_in_fixtures() -> None:
    import inspect

    from tests import llm_fakes

    source = inspect.getsource(llm_fakes)
    assert OPENROUTER_API_KEY not in source and "sk-" not in source


def test_audit_events_carry_no_secret_and_no_unnecessary_private_data(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())
    draft = generate(client, target["id"]).json()
    client.post(f"/api/drafts/{draft['id']}/approve")

    events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type.in_(
                [AuditEventType.DRAFT_GENERATED, AuditEventType.DRAFT_DECIDED]
            )
        )
    ).all()
    assert len(events) == 2
    for event in events:
        assert set(event.details) <= {"reason", "model"}
        for value in event.details.values():
            if isinstance(value, str):
                assert "@" not in value and "\n" not in value and len(value) <= 64
        assert event.subject is not None and "@" not in event.subject


def test_nothing_touches_the_network(client: TestClient, no_network: None) -> None:
    target = scenario(client)
    install(client, FakeLLMClient())

    assert generate(client, target["id"]).status_code == 201


def test_no_real_llm_or_network_library_is_imported_by_the_llm_abstraction() -> None:
    from pathlib import Path

    forbidden = ("openai", "anthropic", "httpx", "requests", "urllib3")
    for file in Path("app/integrations/llm").glob("*.py"):
        source = file.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert f"import {word}" not in source, (file, word)
