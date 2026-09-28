"""Qualification Criteria v1: API routes (4) and security (4) - 8 of the 54 scenarios (see
tests/test_qualification_v1.py for the other 42, tests/test_qualification_v1_cli.py for the CLI).
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.opportunity_qualification_factory import add_target, qualified, run, show, uncertain


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


# === 11. API (4) ==============================================================================


def test_api_run_creates_then_is_idempotent(client: TestClient) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    created = run(client, target["id"])
    assert created.status_code == 201
    unchanged = run(client, target["id"])
    assert unchanged.status_code == 200
    assert unchanged.json()["decision"] == created.json()["decision"] == "qualified"


def test_api_get_qualification_returns_the_current_decision_with_staleness(
    client: TestClient,
) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    run(client, target["id"])
    response = show(client, target["id"])
    assert response.status_code == 200
    assert response.json()["stale"] is False
    assert response.json()["decision"] == "qualified"


def test_api_qualified_endpoint_lists_only_qualified_targets(client: TestClient) -> None:
    good = add_target(client, domain="good.example.invalid", title="Backend Developer Alternance")
    bad = add_target(
        client, domain="bad.example.invalid", contract_type="full_time", title="Backend Developer"
    )
    run(client, good["id"])
    run(client, bad["id"])
    ids = [row["target_id"] for row in qualified(client).json()]
    assert good["id"] in ids
    assert bad["id"] not in ids


def test_api_uncertain_endpoint_lists_only_uncertain_targets(client: TestClient) -> None:
    ambiguous = add_target(
        client,
        domain="ambiguous.example.invalid",
        title="Coordinateur Regional Alternance",
        description="Vous accompagnerez les equipes locales.",
    )
    qualifying = add_target(
        client, domain="clear.example.invalid", title="Backend Developer Alternance"
    )
    run(client, ambiguous["id"])
    run(client, qualifying["id"])
    ids = [row["target_id"] for row in uncertain(client).json()]
    assert ambiguous["id"] in ids
    assert qualifying["id"] not in ids


# === 13. Securite (4) ==========================================================================


def test_securite_every_route_requires_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    for method, path in [
        ("post", "/api/opportunities/1/qualification"),
        ("get", "/api/opportunities/1/qualification"),
        ("get", "/api/opportunities/qualified"),
        ("get", "/api/opportunities/uncertain"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_securite_no_network_capable_import_in_the_new_modules() -> None:
    forbidden = (
        "httpx",
        "requests",
        "http.client",
        "urllib.request",
        "socket",
        "openai",
        "anthropic",
    )
    modules = [
        "app/services/opportunity_qualification.py",
        "app/services/criteria_v1.py",
        "app/services/role_family.py",
        "app/services/location_tier.py",
        "app/services/role_taxonomy.py",
        "app/api/opportunity_qualification.py",
        "app/cli/qualification.py",
    ]
    for module in modules:
        source = Path(module).read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, (module, name)


def test_securite_adversarial_offer_text_is_handled_safely(client: TestClient) -> None:
    target = add_target(
        client,
        title="Backend Developer Alternance",
        description="'; DROP TABLE targets; -- <script>alert(1)</script>",
    )
    response = run(client, target["id"])
    assert response.status_code == 201
    assert response.json()["decision"] == "qualified"
    # The data survived as inert text: the qualified list still works afterwards.
    assert client.get("/api/candidate").status_code == 200


def test_securite_no_private_candidate_data_in_a_qualification_response(
    client: TestClient, candidate_payload: dict[str, Any]
) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    body = run(client, target["id"]).text
    assert candidate_payload["email"] not in body
    assert candidate_payload["first_name"] not in body
