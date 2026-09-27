"""CompanyResearchService: builds the query, calls the provider, returns its result unchanged.

Boundary tests (Part 9): a `ResearchResult` never lets the caller create a qualification, declare
a candidate compatible, decide on an application, or send an e-mail - there is no code path from
one to the other, only data.
"""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.core.secrets import PERPLEXITY_API_KEY, InMemorySecretStore
from app.integrations.research.ports import (
    Observation,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
    ResearchSubject,
)
from app.models import AuditEvent, Company, Qualification, Target
from app.services.company_research import CompanyResearchService, default_research_provider
from tests import targets_factory as f

FIXTURE_QUERY = ResearchQuery(
    objective="fixture objective", subject=ResearchSubject(company_name="Fixture Corp")
)


class StaticProvider:
    """A fake `ResearchProvider`: returns a fixed result and remembers what it was asked."""

    def __init__(self, result: ResearchResult) -> None:
        self.result = result
        self.asked: list[ResearchQuery] = []

    def research(self, query: ResearchQuery) -> ResearchResult:
        self.asked.append(query)
        return self.result


def fixed_result(**overrides: Any) -> ResearchResult:
    defaults: dict[str, Any] = dict(
        provider="perplexity",
        model="openai/gpt-6-luna",
        query=FIXTURE_QUERY,
        status=ResearchStatus.OK,
        observations=(
            Observation(
                claim="A fictional software company.",
                source_url="https://example-data.invalid/about",
                source_title="About",
                excerpt="A fictional software company based in Paris.",
            ),
        ),
    )
    return ResearchResult(**{**defaults, **overrides})


@pytest.fixture
def company(db_session: Session) -> Company:
    return f.company(db_session, name="Example Data SAS")


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- Request construction ------------------------------------------------------------------


def test_the_query_is_built_from_the_companys_own_record(
    db_session: Session, company: Company
) -> None:
    company.website_url = "https://example-data.invalid"
    company.location = "Paris"
    company.sector = "Data / software"
    db_session.commit()
    provider = StaticProvider(fixed_result())

    CompanyResearchService(db_session, provider).research(
        company.id, objective="Understand its technology environment"
    )

    (sent,) = provider.asked
    assert sent.objective == "Understand its technology environment"
    assert sent.subject.company_name == "Example Data SAS"
    assert sent.subject.website == "https://example-data.invalid"
    assert sent.subject.location == "Paris"
    assert sent.subject.sector == "Data / software"


def test_focus_areas_and_max_results_reach_the_query_unchanged(
    db_session: Session, company: Company
) -> None:
    provider = StaticProvider(fixed_result())

    CompanyResearchService(db_session, provider).research(
        company.id,
        objective="obj",
        focus_areas=("recent news", "hiring signals"),
        max_results=3,
    )

    (sent,) = provider.asked
    assert sent.focus_areas == ("recent news", "hiring signals")
    assert sent.max_results == 3


def test_no_candidate_data_is_ever_part_of_the_query(db_session: Session, company: Company) -> None:
    provider = StaticProvider(fixed_result())

    CompanyResearchService(db_session, provider).research(company.id, objective="obj")

    (sent,) = provider.asked
    dump = str(sent)
    assert "candidate" not in dump.lower()  # ResearchSubject/Query have no such field at all


def test_an_unknown_company_is_refused_before_the_provider_is_called(
    db_session: Session,
) -> None:
    provider = StaticProvider(fixed_result())

    with pytest.raises(NotFoundError):
        CompanyResearchService(db_session, provider).research(999, objective="obj")

    assert provider.asked == []


@pytest.mark.parametrize("max_results", [0, 21])
def test_an_out_of_bounds_max_results_is_refused(
    db_session: Session, company: Company, max_results: int
) -> None:
    provider = StaticProvider(fixed_result())

    with pytest.raises(UnprocessableError):
        CompanyResearchService(db_session, provider).research(
            company.id, objective="obj", max_results=max_results
        )

    assert provider.asked == []


# --- Provider configuration (mirrors app.api.drafts.get_llm_client) ------------------------


def test_without_configuration_the_provider_is_none_and_research_is_refused(
    db_session: Session, company: Company
) -> None:
    settings = Settings(research_enabled=False, perplexity_preset=None)
    provider = default_research_provider(settings, InMemorySecretStore())

    assert provider is None
    with pytest.raises(UnprocessableError):
        CompanyResearchService(db_session, provider).research(company.id, objective="obj")


def test_enabled_but_missing_preset_or_secret_still_gives_none() -> None:
    enabled_no_preset = Settings(research_enabled=True, perplexity_preset=None)
    enabled_no_secret = Settings(research_enabled=True, perplexity_preset="low")

    assert default_research_provider(enabled_no_preset, InMemorySecretStore()) is None
    assert default_research_provider(enabled_no_secret, InMemorySecretStore()) is None


def test_fully_configured_gives_a_real_provider_instance() -> None:
    secrets = InMemorySecretStore()
    secrets.set(PERPLEXITY_API_KEY, "pplx-fixture")
    settings = Settings(research_enabled=True, perplexity_preset="low")

    provider = default_research_provider(settings, secrets)

    assert provider is not None and type(provider).__name__ == "PerplexityClient"


# --- Provenance ------------------------------------------------------------------------------


def test_the_results_provenance_is_returned_unchanged(
    db_session: Session, company: Company
) -> None:
    result = fixed_result()
    provider = StaticProvider(result)

    returned = CompanyResearchService(db_session, provider).research(company.id, objective="obj")

    assert returned.observations == result.observations
    (obs,) = returned.observations
    assert obs.source_url and obs.source_title and obs.excerpt


def test_an_observation_without_a_source_stays_representable_but_never_implied(
    db_session: Session, company: Company
) -> None:
    result = fixed_result(observations=(Observation(claim="An unsourced claim."),))
    provider = StaticProvider(result)

    returned = CompanyResearchService(db_session, provider).research(company.id, objective="obj")

    (obs,) = returned.observations
    assert obs.source_url is None and obs.source_title is None and obs.excerpt is None


# --- Boundary: provider output never becomes a Career-agent decision -----------------------


def test_a_research_call_writes_nothing_to_the_database(
    db_session: Session, company: Company
) -> None:
    provider = StaticProvider(fixed_result())
    before = {
        model: count(db_session, model) for model in (Company, Target, Qualification, AuditEvent)
    }

    CompanyResearchService(db_session, provider).research(company.id, objective="obj")

    after = {
        model: count(db_session, model) for model in (Company, Target, Qualification, AuditEvent)
    }
    assert before == after


def test_the_result_type_has_no_field_resembling_a_business_decision() -> None:
    result = fixed_result()
    forbidden = ("good_target", "match", "apply", "score", "qualif", "compatible")

    dump = vars(result)
    assert not any(bad in str(dump).lower() for bad in forbidden)
    for observation in result.observations:
        assert not any(bad in str(vars(observation)).lower() for bad in forbidden)


def test_research_never_modifies_the_company_it_looked_up(
    db_session: Session, company: Company
) -> None:
    provider = StaticProvider(fixed_result())
    original_name, original_sector = company.name, company.sector

    CompanyResearchService(db_session, provider).research(company.id, objective="obj")

    db_session.expire_all()
    refreshed = db_session.get(Company, company.id)
    assert refreshed is not None
    assert (refreshed.name, refreshed.sector) == (original_name, original_sector)


def test_no_qualification_service_or_mail_module_is_imported_by_the_research_integration() -> None:
    from pathlib import Path

    forbidden_modules = (
        "app.services.qualification",
        "app.services.mail_sender",
        "app.integrations.mail",
    )
    files = [
        *Path("app/integrations/research").glob("*.py"),
        Path("app/services/company_research.py"),
    ]
    for file in files:
        source = file.read_text(encoding="utf-8")
        for module in forbidden_modules:
            assert f"import {module}" not in source and f"from {module}" not in source, (
                file,
                module,
            )
