"""CampaignService (chained, bounded sourcing toward a daily target): stopping conditions,
best-effort enrichment, and the funnel view. Fictional providers only, no network.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.integrations.sourcing.ports import (
    ProviderErrorCode,
    SearchHit,
    SearchQuery,
    SourcingProviders,
    WebSearchResult,
)
from app.models.enums import ApplicationPackageStatus, CampaignStatus
from app.schemas.campaign import CampaignCreate
from app.services.campaign import CampaignService
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import crit, make_profile
from tests.sourcing_fakes import FakeWebProvider, company_hit, hit

PROVIDER = "fake-web"


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture
def profile(client: TestClient) -> dict[str, Any]:
    return make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])


class SequencedWebProvider:
    """Returns a DIFFERENT batch of hits per call (an empty one once exhausted) - simulates a
    real search where later, similar queries increasingly return nothing new."""

    def __init__(self, batches: list[list[SearchHit]]) -> None:
        self._batches = batches
        self.calls = 0

    def search(self, query: SearchQuery) -> WebSearchResult:
        batch = self._batches[self.calls] if self.calls < len(self._batches) else []
        self.calls += 1
        return WebSearchResult(completed=True, hits=tuple(batch), sources_consulted=())


def start(
    session: Session,
    *,
    provider: object,
    daily_target: int = 500,
    max_calls: int = 20,
    max_duration_minutes: int = 60,
    max_consecutive_empty: int = 3,
    research_provider: object | None = None,
    llm: object | None = None,
) -> Any:
    service = CampaignService(
        session,
        SourcingProviders(web={PROVIDER: provider}),  # type: ignore[dict-item]
        research_provider=research_provider,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
    )
    return service.start(
        CampaignCreate(
            provider=PROVIDER,
            daily_target=daily_target,
            max_calls=max_calls,
            max_duration_minutes=max_duration_minutes,
            max_consecutive_empty=max_consecutive_empty,
        ),
        actor="cli",
    )


# --- stopping conditions -------------------------------------------------------------------


def test_stops_when_the_daily_target_is_reached(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider(
        [[company_hit("Company A"), company_hit("Company B")]]  # one call, two new targets
    )

    campaign = start(db_session, provider=provider, daily_target=2, max_calls=20)

    assert campaign.status is CampaignStatus.COMPLETED
    assert campaign.stop_reason == "daily_target_reached"
    assert campaign.calls_made == 1
    assert campaign.targets_created == 2


def test_stops_at_the_call_limit(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider([[company_hit(f"Company {n}")] for n in range(10)])

    campaign = start(db_session, provider=provider, daily_target=2000, max_calls=2)

    assert campaign.status is CampaignStatus.STOPPED_CALL_LIMIT
    assert campaign.stop_reason == "max_calls_reached"
    assert campaign.calls_made == 2
    assert campaign.targets_created == 2


def test_stops_after_consecutive_empty_searches(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider([[company_hit("Only Company")]])  # then empty forever

    campaign = start(
        db_session, provider=provider, daily_target=2000, max_calls=50, max_consecutive_empty=3
    )

    assert campaign.status is CampaignStatus.STOPPED_NO_NEW_RESULTS
    assert campaign.stop_reason == "3_consecutive_empty_searches"
    assert campaign.calls_made == 1 + 3  # one productive round, then 3 empty ones
    assert campaign.targets_created == 1


def test_stops_on_a_provider_error(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = FakeWebProvider(error=ProviderErrorCode.UNAVAILABLE)

    campaign = start(db_session, provider=provider, daily_target=2000, max_calls=50)

    assert campaign.status is CampaignStatus.STOPPED_PROVIDER_ERROR
    assert campaign.stop_reason == "provider_call_failed"
    assert campaign.calls_made == 1


def test_a_campaign_is_linked_to_every_round_it_ran(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    from sqlalchemy import select

    from app.models import SearchRun

    provider = SequencedWebProvider([[company_hit("A")], [company_hit("B")]])

    campaign = start(db_session, provider=provider, daily_target=2000, max_calls=2)

    runs = db_session.scalars(select(SearchRun).where(SearchRun.campaign_id == campaign.id)).all()
    assert len(runs) == 2


# --- enrichment (requirements, contacts, drafts) - best effort, never fatal -----------------


def test_enrichment_extracts_requirements_and_proposes_contacts(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    from app.integrations.research.ports import Observation, ResearchResult, ResearchStatus

    class FakeResearch:
        def __init__(self) -> None:
            self.calls = 0

        def research(self, query: Any) -> ResearchResult:
            self.calls += 1
            return ResearchResult(
                provider="fake",
                model="fake",
                query=query,
                status=ResearchStatus.OK,
                observations=(
                    Observation(claim="Jamie Fixture, Recruiter", source_url="https://x.invalid/1"),
                ),
            )

    offer_hit = hit(
        "Data Analyst Alternance",
        "https://search.example.invalid/o/1",
        company_name="Offer Corp",
        offer_title="Data Analyst Alternance",
    )
    provider = SequencedWebProvider([[offer_hit]])

    campaign = start(
        db_session,
        provider=provider,
        daily_target=1,
        max_calls=5,
        research_provider=FakeResearch(),
    )

    assert campaign.targets_created == 1
    assert campaign.contacts_proposed >= 1


def test_enrichment_generates_a_draft_when_an_llm_is_configured(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    offer_hit = hit(
        "Backend Developer Alternance",
        "https://search.example.invalid/o/2",
        company_name="Draft Corp",
        offer_title="Backend Developer Alternance",
    )
    provider = SequencedWebProvider([[offer_hit]])

    campaign = start(
        db_session, provider=provider, daily_target=1, max_calls=5, llm=FakeLLMClient()
    )

    assert campaign.targets_created == 1
    assert campaign.packages_prepared == 1
    assert campaign.drafts_generated == 1


def test_no_llm_no_research_configured_still_completes_sourcing_and_qualification(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider([[company_hit("Bare Corp")]])

    campaign = start(db_session, provider=provider, daily_target=1, max_calls=5)

    assert campaign.targets_created == 1
    assert campaign.contacts_proposed == 0
    assert campaign.packages_prepared == 0
    assert campaign.drafts_generated == 0


# --- funnel ---------------------------------------------------------------------------------


def test_funnel_reflects_current_state_of_everything_the_campaign_found(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider([[company_hit("Funnel Corp")]])
    campaign = start(db_session, provider=provider, daily_target=1, max_calls=5)

    service = CampaignService(db_session, SourcingProviders())
    funnel = service.funnel(campaign.id)

    assert funnel.targets_total == 1
    assert funnel.qualified + funnel.uncertain + funnel.excluded == 1
    assert funnel.packages_by_status[ApplicationPackageStatus.DRAFT] == 0
    assert funnel.contacts_accepted_with_email == 0  # nothing was ever accepted by a human
    assert funnel.packages_sent == 0


def test_funnel_of_an_unknown_campaign_raises_not_found(
    client: TestClient, db_session: Session
) -> None:
    service = CampaignService(db_session, SourcingProviders())
    with pytest.raises(NotFoundError):
        service.get(999)


# --- read-only API (starting a campaign is CLI-only) ----------------------------------------


def test_the_campaign_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    for path in ("/api/campaigns", "/api/campaigns/1", "/api/campaigns/1/funnel"):
        assert anonymous.get(path).status_code == 401, path


def test_the_api_can_read_a_campaign_started_through_the_service(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    provider = SequencedWebProvider([[company_hit("API Corp")]])
    campaign = start(db_session, provider=provider, daily_target=1, max_calls=5)

    listed = client.get("/api/campaigns")
    assert listed.status_code == 200
    assert any(c["id"] == campaign.id for c in listed.json())

    read = client.get(f"/api/campaigns/{campaign.id}")
    assert read.status_code == 200 and read.json()["status"] == "completed"

    funnel = client.get(f"/api/campaigns/{campaign.id}/funnel")
    assert funnel.status_code == 200 and funnel.json()["targets_total"] == 1
