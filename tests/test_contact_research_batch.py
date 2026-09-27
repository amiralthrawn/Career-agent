"""ContactResearchBatchService (step 8, Part 6): grouping, budget, isolated failures, no
redundant search, idempotence. All synthetic, no network, no real Perplexity call.
"""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.integrations.research.ports import (
    Observation,
    ResearchError,
    ResearchErrorCode,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
    ResearchSubject,
)
from app.models import ContactResearchObservation
from app.models.enums import RoleCategory
from app.services.contact_research_batch import ContactResearchBatchService, ContactSearchOutcome
from tests import targets_factory as f


class ScriptedProvider:
    """A fake `ResearchProvider`: one scripted outcome per (company, focus area); records every
    query. Mirrors `tests.test_research_batch.ScriptedProvider`."""

    def __init__(self, script: dict[tuple[str, str], ResearchResult | ResearchErrorCode]) -> None:
        self._script = script
        self.asked: list[ResearchQuery] = []

    def research(self, query: ResearchQuery) -> ResearchResult:
        self.asked.append(query)
        key = (query.subject.company_name, query.focus_areas[0])
        outcome = self._script[key]
        if isinstance(outcome, ResearchErrorCode):
            raise ResearchError(outcome)
        return outcome


def result(claim: str = "A recruiter.", url: str = "https://x.invalid/a") -> ResearchResult:
    return ResearchResult(
        provider="perplexity",
        model="fake",
        query=ResearchQuery(objective="obj", subject=ResearchSubject(company_name="placeholder")),
        status=ResearchStatus.OK,
        observations=(Observation(claim=claim, source_url=url),),
    )


@pytest.fixture
def two_companies(db_session: Session) -> dict[str, Any]:
    candidate = f.candidate(db_session)
    company_a = f.company(db_session, name="Company A", domain="a.invalid")
    company_b = f.company(db_session, name="Company B", domain="b.invalid")
    # Two DIFFERENT offers at the same company: only one SPONTANEOUS target per company is
    # allowed, so a second target of company A needs its own opportunity.
    offer_a2 = f.opportunity(db_session, company_a.id, title="Second Role")
    target_a1 = f.target(db_session, candidate.id, company_a.id)
    target_a2 = f.target(db_session, candidate.id, company_a.id, offer_a2.id)
    target_b = f.target(db_session, candidate.id, company_b.id)
    return {
        "candidate_id": candidate.id,
        "company_a": company_a.id,
        "company_b": company_b.id,
        "target_a1": target_a1.id,
        "target_a2": target_a2.id,
        "target_b": target_b.id,
    }


# --- grouping: one call per (company, category), fanned out to every waiting target ------------


def test_two_targets_of_the_same_company_share_a_single_search(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    provider = ScriptedProvider(
        {("Company A", "recruiter or talent acquisition contact"): result()}
    )

    report = ContactResearchBatchService(db_session, provider).run(
        target_ids=[two_companies["target_a1"], two_companies["target_a2"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    assert len(provider.asked) == 1  # ONE provider call for two targets of the same company
    (item,) = report.items
    assert item.outcome is ContactSearchOutcome.RESEARCHED
    assert sorted(item.targets_affected) == sorted(
        [two_companies["target_a1"], two_companies["target_a2"]]
    )
    # both targets got their own, independent observation row
    observations = db_session.query(ContactResearchObservation).all()
    assert {o.target_id for o in observations} == {
        two_companies["target_a1"],
        two_companies["target_a2"],
    }


def test_no_search_when_a_contact_of_that_category_is_already_known(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    contact = f.contact(db_session, two_companies["company_a"], name="Already Known")
    contact.role_category = RoleCategory.RECRUITER
    db_session.commit()
    provider = ScriptedProvider({})  # never consulted for company A

    report = ContactResearchBatchService(db_session, provider).run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    assert provider.asked == []
    (item,) = report.items
    assert item.outcome is ContactSearchOutcome.ALREADY_KNOWN
    assert report.searches_skipped_already_known == 1
    assert report.searches_researched == 0


# --- isolated failure ---------------------------------------------------------------------------


def test_a_provider_failure_on_one_company_never_stops_the_others(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    provider = ScriptedProvider(
        {
            ("Company A", "recruiter or talent acquisition contact"): ResearchErrorCode.UNAVAILABLE,
            ("Company B", "recruiter or talent acquisition contact"): result(
                "A contact.", "https://x.invalid/b"
            ),
        }
    )

    report = ContactResearchBatchService(db_session, provider).run(
        target_ids=[two_companies["target_a1"], two_companies["target_b"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    outcomes = {item.company_id: item.outcome for item in report.items}
    assert outcomes[two_companies["company_a"]] is ContactSearchOutcome.RESEARCH_FAILED
    assert outcomes[two_companies["company_b"]] is ContactSearchOutcome.RESEARCHED
    assert report.searches_failed == 1 and report.searches_researched == 1


# --- budget ----------------------------------------------------------------------------------


def test_the_budget_limits_how_many_companies_are_researched(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    provider = ScriptedProvider(
        {
            ("Company A", "recruiter or talent acquisition contact"): result(
                "A.", "https://x.invalid/a"
            ),
            ("Company B", "recruiter or talent acquisition contact"): result(
                "B.", "https://x.invalid/b"
            ),
        }
    )

    report = ContactResearchBatchService(db_session, provider).run(
        target_ids=[
            two_companies["target_a1"],
            two_companies["target_a2"],
            two_companies["target_b"],
        ],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=1,
    )

    assert len(provider.asked) == 1
    assert report.searches_researched == 1
    assert report.searches_skipped_budget == 1
    # deterministic priority: company A affects 2 targets, so it is researched first
    researched = [i for i in report.items if i.outcome is ContactSearchOutcome.RESEARCHED]
    assert researched[0].company_id == two_companies["company_a"]


def test_zero_budget_skips_every_search_and_calls_no_provider(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    provider = ScriptedProvider({})

    report = ContactResearchBatchService(db_session, provider).run(
        target_ids=[two_companies["target_a1"]], role_categories=[RoleCategory.RECRUITER]
    )

    assert provider.asked == []
    assert all(i.outcome is ContactSearchOutcome.SKIPPED_BUDGET for i in report.items)


def test_no_provider_configured_skips_every_search(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    report = ContactResearchBatchService(db_session, None).run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    assert all(i.outcome is ContactSearchOutcome.SKIPPED_BUDGET for i in report.items)


# --- idempotence -------------------------------------------------------------------------------


def test_running_the_batch_twice_never_duplicates_an_observation(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    provider = ScriptedProvider(
        {("Company A", "recruiter or talent acquisition contact"): result()}
    )
    service = ContactResearchBatchService(db_session, provider)

    service.run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )
    service.run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    assert len(provider.asked) == 2  # the search itself is repeated (nothing is known yet)...
    observations = db_session.query(ContactResearchObservation).all()
    assert len(observations) == 1  # ...but the SAME finding is never stored twice


def test_idempotence_survives_an_earlier_provider_failure(
    db_session: Session, two_companies: dict[str, Any]
) -> None:
    service = ContactResearchBatchService(
        db_session,
        ScriptedProvider(
            {("Company A", "recruiter or talent acquisition contact"): ResearchErrorCode.TIMEOUT}
        ),
    )
    first = service.run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )
    assert first.searches_failed == 1

    service_retry = ContactResearchBatchService(
        db_session,
        ScriptedProvider({("Company A", "recruiter or talent acquisition contact"): result()}),
    )
    second = service_retry.run(
        target_ids=[two_companies["target_a1"]],
        role_categories=[RoleCategory.RECRUITER],
        max_research_calls=5,
    )

    assert second.searches_researched == 1
    assert len(db_session.query(ContactResearchObservation).all()) == 1


# --- no score, no autonomous contact -------------------------------------------------------------


def test_the_report_has_no_score_or_ranking_field() -> None:
    from dataclasses import fields as dataclass_fields

    from app.services.contact_research_batch import ContactBatchItem, ContactBatchReport

    names = {field.name for field in dataclass_fields(ContactBatchReport)}
    names |= {field.name for field in dataclass_fields(ContactBatchItem)}
    assert not any(bad in name for name in names for bad in ("score", "rank"))
