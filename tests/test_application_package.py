"""ApplicationPackage (step 9): evidence-first application workflow, HITL, staleness, isolation.
All synthetic, no network, no real Perplexity/OpenRouter/GitHub call.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.github.ports import (
    GitHubError,
    GitHubErrorCode,
    GitHubRepository,
    GitHubResult,
    GitHubStatus,
)
from app.models import (
    ApplicationDraft,
    ApplicationPackage,
    AuditEvent,
    CompanyResearchFact,
    Contact,
    ContactChannel,
    ContactResearchObservation,
    DocumentIngestion,
    Target,
    TargetContact,
)
from app.models.audit import AuditEventType
from app.models.enums import (
    ApplicationPackageStatus,
    ChannelKind,
    InfoStatus,
    ProposalStatus,
    RoleCategory,
)
from app.schemas.application_package import ApplicationPrepareRequest
from app.services.application_package import ApplicationPackageService
from tests import targets_factory as f
from tests.llm_fakes import FakeLLMClient, echo_context
from tests.qualification_factory import add_target, qualify
from tests.requirements_factory import scenario


class StaticGitHub:
    """A fake `GitHubProvider`: returns a fixed result, or raises a fixed error."""

    def __init__(self, result: GitHubResult | GitHubErrorCode) -> None:
        self._result = result
        self.asked: list[str] = []

    def research(self, username: str) -> GitHubResult:
        self.asked.append(username)
        if isinstance(self._result, GitHubErrorCode):
            raise GitHubError(self._result)
        return self._result


def github_repo(**overrides: Any) -> GitHubRepository:
    defaults: dict[str, Any] = dict(
        name="Commodity-Arbitrage-Analysis",
        url="https://github.com/fixture-user/Commodity-Arbitrage-Analysis",
        description="Python and SQL analysis of commodity price arbitrage.",
        primary_language="Python",
        languages=("Python", "SQL"),
        source_url="https://api.github.com/repos/fixture-user/Commodity-Arbitrage-Analysis",
    )
    return GitHubRepository(**{**defaults, **overrides})


def github_result(*repos: GitHubRepository) -> GitHubResult:
    status = GitHubStatus.OK if repos else GitHubStatus.NO_RESULTS
    return GitHubResult(username="fixture-user", status=status, repositories=tuple(repos))


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def candidate_id_of(session: Session) -> int:
    result = session.scalars(select(Target.candidate_id)).first()
    assert result is not None
    return result


def ingest_cv(session: Session, candidate_id: int, sha256: str = "a" * 64) -> DocumentIngestion:
    doc = DocumentIngestion(
        candidate_id=candidate_id,
        source_uri="documents/cv.docx",
        sha256=sha256,
        file_size=100,
        parser_version="v1",
        stats={},
    )
    session.add(doc)
    session.flush()
    return doc


def accept_contact(
    session: Session,
    target: Target,
    *,
    do_not_contact: bool = False,
    role_category: RoleCategory = RoleCategory.RECRUITER,
    with_channel: bool = True,
) -> Contact:
    contact = f.contact(session, target.company_id, name="Jamie Fixture")
    contact.role_category = role_category
    contact.do_not_contact = do_not_contact
    if with_channel:
        session.add(
            ContactChannel(
                contact_id=contact.id,
                kind=ChannelKind.EMAIL,
                value="jamie@fixture-corp.example.invalid",
                status=InfoStatus.FOUND,
                source_id=f.source(session).id,
            )
        )
    session.add(
        TargetContact(
            target_id=target.id,
            contact_id=contact.id,
            company_id=target.company_id,
            is_primary=True,
        )
    )
    session.flush()
    return contact


def add_company_fact(
    session: Session, company_id: int, claim: str = "Uses Python and SQL."
) -> CompanyResearchFact:
    fact = CompanyResearchFact(
        company_id=company_id,
        claim=claim,
        source_url="https://x.invalid/about",
        source_title="About",
        excerpt=claim,
        retrieved_at=datetime.now(UTC),
    )
    session.add(fact)
    session.flush()
    return fact


def service(
    db_session: Session,
    *,
    llm: FakeLLMClient | None = None,
    github: StaticGitHub | None = None,
    github_username: str | None = "fixture-user",
) -> ApplicationPackageService:
    return ApplicationPackageService(db_session, llm, github, github_username)


# --- basic preparation ---------------------------------------------------------------------


def test_prepare_creates_a_package_for_a_qualified_target(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    candidate_id = candidate_id_of(db_session)
    ingest_cv(db_session, candidate_id)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.status is ApplicationPackageStatus.PENDING_VALIDATION
    assert package.draft_id is not None
    assert package.cv_document_id is not None
    assert package.contact_id is None
    codes = {w["code"] for w in package.warnings}
    assert "no_accepted_contact" in codes


def test_a_never_qualified_target_is_refused(client: TestClient, db_session: Session) -> None:
    target = f.target(db_session, f.candidate(db_session).id, f.company(db_session).id)

    with pytest.raises(NotFoundError):
        service(db_session, llm=FakeLLMClient()).prepare(target.id, ApplicationPrepareRequest())


def test_a_stale_qualification_refuses_preparation(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    client.post("/api/candidate/skills", json={"name": "Docker"})  # Brain moved on

    with pytest.raises(UnprocessableError):
        service(db_session, llm=FakeLLMClient()).prepare(target["id"], ApplicationPrepareRequest())


def test_no_cv_reference_is_a_warning_not_a_block(client: TestClient, db_session: Session) -> None:
    target = scenario(client)  # no CV ingested

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.status is ApplicationPackageStatus.PENDING_VALIDATION
    assert package.cv_document_id is None
    assert any(w["code"] == "no_cv_reference" for w in package.warnings)


def test_the_cv_document_row_is_never_modified(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    candidate_id = candidate_id_of(db_session)
    doc = ingest_cv(db_session, candidate_id)
    original_sha = doc.sha256

    service(db_session, llm=FakeLLMClient()).prepare(target["id"], ApplicationPrepareRequest())

    db_session.expire_all()
    refreshed = db_session.get(DocumentIngestion, doc.id)
    assert refreshed is not None and refreshed.sha256 == original_sha


def test_no_private_file_is_ever_read_by_this_module() -> None:
    from pathlib import Path

    source = Path("app/services/application_package.py").read_text(encoding="utf-8")
    for banned in ("resolve_private_file", "read_document_bytes", "smtplib", "send_mail"):
        assert banned not in source


# --- contact --------------------------------------------------------------------------------


def test_an_accepted_contact_is_used_in_the_personalization_context(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    target_row = db_session.get(Target, target["id"])
    assert target_row is not None
    contact = accept_contact(db_session, target_row, role_category=RoleCategory.TECH)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.contact_id == contact.id
    ctx = package.personalization_context["contact"]
    assert ctx["role_category"] == "tech"
    assert ctx["channel"]["value"] == "jamie@fixture-corp.example.invalid"
    assert "approach_hint" in ctx and ctx["approach_hint"]


def test_a_pending_observation_is_never_used_as_a_real_contact(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    target_row = db_session.get(Target, target["id"])
    assert target_row is not None
    db_session.add(
        ContactResearchObservation(
            target_id=target_row.id,
            company_id=target_row.company_id,
            requested_role_category=RoleCategory.RECRUITER,
            status=ProposalStatus.PENDING,
            claim="Someone recruits here.",
            source_url="https://x.invalid/a",
            retrieved_at=datetime.now(UTC),
            fingerprint="fp1",
        )
    )
    db_session.flush()

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.contact_id is None
    assert package.personalization_context["contact"] is None


def test_a_do_not_contact_contact_is_never_selected(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    target_row = db_session.get(Target, target["id"])
    assert target_row is not None
    accept_contact(db_session, target_row, do_not_contact=True)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.contact_id is None
    assert any(w["code"] == "no_accepted_contact" for w in package.warnings)


def test_a_contact_without_a_channel_never_gets_one_invented(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    target_row = db_session.get(Target, target["id"])
    assert target_row is not None
    accept_contact(db_session, target_row, with_channel=False)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.personalization_context["contact"]["channel"] is None


# --- GitHub evidence --------------------------------------------------------------------------


def test_github_evidence_is_sourced_and_matched(client: TestClient, db_session: Session) -> None:
    target = scenario(client)  # offer requires Python, SQL, Power BI

    package = service(
        db_session, llm=FakeLLMClient(), github=StaticGitHub(github_result(github_repo()))
    ).prepare(target["id"], ApplicationPrepareRequest())

    evidence = package.personalization_context["github_evidence"]
    assert evidence and evidence[0]["repository_name"] == "Commodity-Arbitrage-Analysis"
    assert evidence[0]["is_personal_project"] is True
    assert evidence[0]["source_url"]
    assert "Python" in evidence[0]["matched_requirement_labels"]


def test_github_unavailable_never_blocks_the_package(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)

    package = service(
        db_session, llm=FakeLLMClient(), github=StaticGitHub(GitHubErrorCode.UNAVAILABLE)
    ).prepare(target["id"], ApplicationPrepareRequest())

    assert package.status is ApplicationPackageStatus.PENDING_VALIDATION
    assert package.personalization_context["github_evidence"] == []
    assert any(w["code"] == "github_unavailable" for w in package.warnings)


def test_no_repository_is_ever_invented_without_a_match(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    unrelated = github_repo(
        name="Unrelated-Repo",
        description="A game written in Rust",
        primary_language="Rust",
        languages=("Rust",),
    )

    package = service(
        db_session, llm=FakeLLMClient(), github=StaticGitHub(github_result(unrelated))
    ).prepare(target["id"], ApplicationPrepareRequest())

    assert package.personalization_context["github_evidence"] == []


def test_no_company_research_gives_empty_evidence_never_invented(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    assert package.personalization_context["company_evidence"] == []


def test_company_research_facts_are_used_when_present(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    add_company_fact(db_session, target["company"]["id"])
    qualify(client, target["id"])  # new company research: requalify first (step 7's own rule)

    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    (fact,) = package.personalization_context["company_evidence"]
    assert (
        fact["claim"] == "Uses Python and SQL." and fact["source_url"] == "https://x.invalid/about"
    )


# --- claims traceability / no candidate data leak ---------------------------------------------


def test_claims_stay_candidate_brain_only_never_github_or_company_facts(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    add_company_fact(db_session, target["company"]["id"], claim="A very distinctive company fact.")
    qualify(client, target["id"])
    fake = FakeLLMClient()

    package = service(
        db_session, llm=fake, github=StaticGitHub(github_result(github_repo()))
    ).prepare(target["id"], ApplicationPrepareRequest())

    draft = db_session.get(ApplicationDraft, package.draft_id)
    assert draft is not None
    dump = str(draft.claims) + str(draft.selected_evidence)
    assert "Commodity-Arbitrage-Analysis" not in dump
    assert "very distinctive company fact" not in dump


def test_the_provider_never_receives_more_than_the_vetted_context(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    target_row = db_session.get(Target, target["id"])
    assert target_row is not None
    accept_contact(db_session, target_row)
    add_company_fact(db_session, target["company"]["id"])
    qualify(client, target["id"])
    fake = FakeLLMClient(text=echo_context)

    service(db_session, llm=fake, github=StaticGitHub(github_result(github_repo()))).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    (sent,) = fake.requests
    assert set(sent.context) <= {
        "mode",
        "company_name",
        "offer_title",
        "contract_type",
        "strengths",
        "do_not_claim",
        "company_context",
        "company_evidence",
        "contact",
        "github_evidence",
    }
    assert "candidate_id" not in str(sent.context)


# --- human-in-the-loop --------------------------------------------------------------------------


def test_no_llm_configured_keeps_the_package_in_draft_status(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)

    package = service(db_session, llm=None).prepare(target["id"], ApplicationPrepareRequest())

    assert package.status is ApplicationPackageStatus.DRAFT
    assert package.draft_id is None


def test_a_draft_status_package_cannot_be_approved_or_rejected(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    svc = service(db_session, llm=None)
    package = svc.prepare(target["id"], ApplicationPrepareRequest())

    with pytest.raises(ConflictError):
        svc.decide(package.id, approve=True)


def test_preparing_again_once_an_llm_is_available_completes_the_draft_package(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    svc_no_llm = service(db_session, llm=None)
    first = svc_no_llm.prepare(target["id"], ApplicationPrepareRequest())
    assert first.status is ApplicationPackageStatus.DRAFT

    svc_with_llm = service(db_session, llm=FakeLLMClient())
    second = svc_with_llm.prepare(target["id"], ApplicationPrepareRequest())

    assert second.id != first.id
    assert second.status is ApplicationPackageStatus.PENDING_VALIDATION
    db_session.expire_all()
    superseded = db_session.get(ApplicationPackage, first.id)
    assert superseded is not None and superseded.status is ApplicationPackageStatus.SUPERSEDED
    assert superseded.superseded_by_id == second.id


def test_approve_then_reject_is_refused(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    svc = service(db_session, llm=FakeLLMClient())
    package = svc.prepare(target["id"], ApplicationPrepareRequest())

    approved = svc.decide(package.id, approve=True)
    assert approved.status is ApplicationPackageStatus.APPROVED and approved.decided_at is not None

    with pytest.raises(ConflictError):
        svc.decide(package.id, approve=False)


def test_reject_is_terminal_and_never_sends_anything(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    svc = service(db_session, llm=FakeLLMClient())
    package = svc.prepare(target["id"], ApplicationPrepareRequest())

    rejected = svc.decide(package.id, approve=False)

    assert rejected.status is ApplicationPackageStatus.REJECTED
    assert not hasattr(svc, "send")


def test_package_content_is_immutable(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    svc = service(db_session, llm=FakeLLMClient())
    package = svc.prepare(target["id"], ApplicationPrepareRequest())

    package.warnings = [{"code": "tampered", "text": "x"}]
    with pytest.raises(Exception):  # noqa: B017 - the DB trigger raises a backend-specific error
        db_session.commit()
    db_session.rollback()


# --- idempotence and staleness -----------------------------------------------------------------


def test_preparing_twice_with_nothing_changed_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    fake = FakeLLMClient()
    svc = service(db_session, llm=fake)

    first = svc.prepare(target["id"], ApplicationPrepareRequest())
    second = svc.prepare(target["id"], ApplicationPrepareRequest())

    assert first.id == second.id
    assert len(fake.requests) == 1  # no wasted LLM call


def test_new_company_research_makes_the_package_stale_and_reprepare_supersedes(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    svc = service(db_session, llm=FakeLLMClient())
    first = svc.prepare(target["id"], ApplicationPrepareRequest())
    assert svc.is_stale(first) is False

    add_company_fact(db_session, target["company"]["id"])

    assert svc.is_stale(first) is True
    qualify(client, target["id"])
    second = svc.prepare(target["id"], ApplicationPrepareRequest())
    assert second.id != first.id
    db_session.expire_all()
    superseded = db_session.get(ApplicationPackage, first.id)
    assert superseded is not None and superseded.status is ApplicationPackageStatus.SUPERSEDED


# --- isolation -----------------------------------------------------------------------------------


def test_two_targets_of_different_companies_never_mix_evidence(
    client: TestClient, db_session: Session
) -> None:
    target_a = scenario(client)
    target_b_json = add_target(client, domain="other-corp.example.invalid", name="Other Corp")
    qualify(client, target_b_json["id"])
    add_company_fact(db_session, target_a["company"]["id"], claim="Fact only for company A.")
    qualify(client, target_a["id"])

    svc = service(db_session, llm=FakeLLMClient())
    package_a = svc.prepare(target_a["id"], ApplicationPrepareRequest())
    package_b = svc.prepare(target_b_json["id"], ApplicationPrepareRequest())

    assert package_a.personalization_context["company_evidence"]
    assert package_b.personalization_context["company_evidence"] == []


# --- no score, audit -------------------------------------------------------------------------


def test_no_score_or_ranking_field_on_the_package(client: TestClient, db_session: Session) -> None:
    target = scenario(client)
    package = service(db_session, llm=FakeLLMClient()).prepare(
        target["id"], ApplicationPrepareRequest()
    )

    dump = str(package.personalization_context) + str(package.warnings)
    assert not any(bad in dump.lower() for bad in ("score", "rank", "best_match"))


def test_audit_events_are_recorded_and_carry_no_secret(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    svc = service(db_session, llm=FakeLLMClient())
    package = svc.prepare(target["id"], ApplicationPrepareRequest())
    svc.decide(package.id, approve=True)

    events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type.in_(
                [AuditEventType.APPLICATION_PREPARED, AuditEventType.APPLICATION_DECIDED]
            )
        )
    ).all()
    assert len(events) == 2
    for event in events:
        for value in event.details.values():
            if isinstance(value, str):
                assert "@" not in value and len(value) <= 64
