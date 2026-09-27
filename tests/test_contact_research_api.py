"""Contact research routes (step 8): thin HTTP layer over `ContactResearchService` /
`ContactResearchBatchService`. Only `StaticProvider` is used here: no test reachable from this
file contacts Perplexity.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.contact_research import get_research_provider
from app.integrations.research.ports import (
    Observation,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
    ResearchSubject,
)
from app.models.enums import ChannelKind, ProposalStatus, RoleCategory
from tests.qualification_factory import add_target

FIXTURE_QUERY = ResearchQuery(
    objective="fixture", subject=ResearchSubject(company_name="Fixture Corp")
)


class StaticProvider:
    def __init__(self, result: ResearchResult) -> None:
        self.result = result

    def research(self, query: ResearchQuery) -> ResearchResult:
        return self.result


def fixed_result(**overrides: Any) -> ResearchResult:
    defaults: dict[str, Any] = dict(
        provider="perplexity",
        model="fake",
        query=FIXTURE_QUERY,
        status=ResearchStatus.OK,
        observations=(Observation(claim="A recruiter.", source_url="https://x.invalid/team"),),
    )
    return ResearchResult(**{**defaults, **overrides})


def install(client: TestClient, provider: StaticProvider | None) -> None:
    client.app.dependency_overrides[get_research_provider] = lambda: provider  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def test_search_creates_pending_observations(client: TestClient) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))

    response = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    )

    assert response.status_code == 201, response.text
    (observation,) = response.json()
    assert observation["status"] == "pending"
    assert observation["source_url"] == "https://x.invalid/team"


def test_without_a_configured_provider_search_is_refused(client: TestClient) -> None:
    target = add_target(client)
    client.app.dependency_overrides.pop(get_research_provider, None)  # type: ignore[attr-defined]

    response = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    )

    assert response.status_code == 422


def test_list_get_accept_reject_round_trip(client: TestClient) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))
    (observation,) = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    ).json()

    listed = client.get(
        "/api/contact-research/observations", params={"target_id": target["id"]}
    ).json()
    assert [o["id"] for o in listed] == [observation["id"]]

    fetched = client.get(f"/api/contact-research/observations/{observation['id']}").json()
    assert fetched["id"] == observation["id"]

    accepted = client.post(
        f"/api/contact-research/observations/{observation['id']}/accept",
        json={
            "full_name": "Alex Fixture",
            "role_title": "Recruiter",
            "channel_kind": "email",
            "channel_value": "alex@fixture-corp.example.invalid",
        },
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted" and body["resulting_contact_id"] is not None

    contacts = client.get(f"/api/companies/{target['company']['id']}/contacts").json()
    (contact,) = [c for c in contacts if c["id"] == body["resulting_contact_id"]]
    assert contact["full_name"] == "Alex Fixture"
    assert contact["email"]["value"] == "alex@fixture-corp.example.invalid"
    assert contact["verified"] is False


def test_reject_leaves_no_contact(client: TestClient) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))
    (observation,) = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    ).json()

    response = client.post(
        f"/api/contact-research/observations/{observation['id']}/reject",
        json={"note": "Not relevant"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["resulting_contact_id"] is None


def test_a_decided_observation_cannot_be_decided_again(client: TestClient) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))
    (observation,) = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    ).json()
    client.post(f"/api/contact-research/observations/{observation['id']}/reject", json={})

    again = client.post(
        f"/api/contact-research/observations/{observation['id']}/accept",
        json={"full_name": "Too Late"},
    )

    assert again.status_code == 409


def test_batch_run_is_reachable_and_reports_no_score(client: TestClient) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))

    response = client.post(
        "/api/contact-research/batch",
        json={"target_ids": [target["id"]], "max_research_calls": 5},
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["searches_researched"] >= 1
    assert not any("score" in key or "rank" in key for key in report)


def test_there_is_no_send_route(client: TestClient) -> None:
    for path in ("/api/contact-research/observations/1/send", "/api/contact-research/send"):
        assert client.post(path).status_code in (404, 405)


def test_the_contact_research_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/targets/1/contact-research"),
        ("get", "/api/contact-research/observations"),
        ("get", "/api/contact-research/observations/1"),
        ("post", "/api/contact-research/observations/1/accept"),
        ("post", "/api/contact-research/observations/1/reject"),
        ("post", "/api/contact-research/batch"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_nothing_touches_the_network(client: TestClient, no_network: None) -> None:
    target = add_target(client)
    install(client, StaticProvider(fixed_result()))

    response = client.post(
        f"/api/targets/{target['id']}/contact-research",
        json={"role_categories": ["recruiter"]},
    )

    assert response.status_code == 201


def test_role_category_enum_matches_the_requested_categories() -> None:
    """Sanity check: every category the task asks for is representable without a new enum."""
    wanted = {
        RoleCategory.RECRUITER,
        RoleCategory.HR,
        RoleCategory.MANAGER,
        RoleCategory.TECH,
        RoleCategory.OTHER,
    }
    assert wanted <= set(RoleCategory)
    assert ChannelKind.EMAIL in set(ChannelKind)
    assert set(ProposalStatus) == {
        ProposalStatus.PENDING,
        ProposalStatus.ACCEPTED,
        ProposalStatus.REJECTED,
    }
