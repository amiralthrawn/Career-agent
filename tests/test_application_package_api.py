"""Application package routes (step 9): thin HTTP layer, auth, no send route.

Only `FakeLLMClient`/a fake GitHub provider are used here: no test reachable from this file
contacts OpenRouter, Perplexity or GitHub for real.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.applications import get_github_provider
from app.api.drafts import get_llm_client
from tests.llm_fakes import FakeLLMClient
from tests.requirements_factory import scenario


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def install_llm(client: TestClient, llm: FakeLLMClient | None) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: llm  # type: ignore[attr-defined]


def test_prepare_get_approve_round_trip(client: TestClient) -> None:
    target = scenario(client)
    install_llm(client, FakeLLMClient())
    client.app.dependency_overrides.pop(get_github_provider, None)  # type: ignore[attr-defined]

    prepared = client.post(f"/api/applications/{target['id']}/prepare", json={})
    assert prepared.status_code == 201, prepared.text
    body = prepared.json()
    assert body["status"] == "pending_validation"
    assert body["personalization_context"]["company_evidence"] == []
    assert body["personalization_context"]["contact"] is None

    fetched = client.get(f"/api/applications/{body['id']}")
    assert fetched.status_code == 200 and fetched.json()["id"] == body["id"]

    approved = client.post(f"/api/applications/{body['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_reject_is_reachable_and_terminal(client: TestClient) -> None:
    target = scenario(client)
    install_llm(client, FakeLLMClient())

    body = client.post(f"/api/applications/{target['id']}/prepare", json={}).json()
    rejected = client.post(f"/api/applications/{body['id']}/reject")

    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"

    again = client.post(f"/api/applications/{body['id']}/approve")
    assert again.status_code == 409


def test_without_an_llm_client_the_package_stays_draft(client: TestClient) -> None:
    target = scenario(client)
    client.app.dependency_overrides.pop(get_llm_client, None)  # type: ignore[attr-defined]

    body = client.post(f"/api/applications/{target['id']}/prepare", json={}).json()

    assert body["status"] == "draft"
    assert body["draft_id"] is None


def test_a_stale_qualification_refuses_preparation(client: TestClient) -> None:
    target = scenario(client)
    install_llm(client, FakeLLMClient())
    client.post("/api/candidate/skills", json={"name": "Docker"})

    response = client.post(f"/api/applications/{target['id']}/prepare", json={})

    assert response.status_code == 422


def test_there_is_no_send_route(client: TestClient) -> None:
    for path in ("/api/applications/1/send", "/api/applications/send"):
        assert client.post(path).status_code in (404, 405)


def test_the_application_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/applications/1/prepare"),
        ("get", "/api/applications/1"),
        ("post", "/api/applications/1/approve"),
        ("post", "/api/applications/1/reject"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_nothing_touches_the_network(client: TestClient, no_network: None) -> None:
    target = scenario(client)
    install_llm(client, FakeLLMClient())

    response = client.post(f"/api/applications/{target['id']}/prepare", json={})

    assert response.status_code == 201
