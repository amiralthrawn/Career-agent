"""API-level tests of the Candidate Brain (API -> service -> repository -> model)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

BASE = "/api/candidate"

FACT_ENDPOINTS: list[tuple[str, dict[str, Any]]] = [
    ("education", {"institution": "Fixture Institute", "status": "in_progress"}),
    ("experiences", {"company": "Fixture Co", "title": "Fixture Role"}),
    ("projects", {"name": "Fixture Project", "url": "https://example.invalid/p"}),
    ("skills", {"name": "Fixture Skill", "category": "fixture"}),
    ("certifications", {"name": "Fixture Cert"}),
    ("languages", {"language": "Fixture Language"}),
]


@pytest.fixture
def created_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(BASE, json=candidate_payload)
    assert response.status_code == 201
    result: dict[str, Any] = response.json()
    return result


def post_ok(client: TestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"{BASE}/{path}", json=payload)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


# --- Candidate -----------------------------------------------------------------------


def test_get_candidate_before_creation_is_404(client: TestClient) -> None:
    assert client.get(BASE).status_code == 404


def test_candidate_creation_and_retrieval(
    client: TestClient, candidate_payload: dict[str, Any]
) -> None:
    created = client.post(BASE, json=candidate_payload)

    assert created.status_code == 201
    fetched = client.get(BASE).json()
    assert fetched["id"] == created.json()["id"]
    assert fetched["first_name"] == candidate_payload["first_name"]


def test_only_one_candidate_can_exist(
    client: TestClient, created_candidate: dict[str, Any], candidate_payload: dict[str, Any]
) -> None:
    assert client.post(BASE, json=candidate_payload).status_code == 409


def test_candidate_validation(client: TestClient) -> None:
    assert client.post(BASE, json={"first_name": "Test"}).status_code == 422
    bad_email = {"first_name": "T", "last_name": "C", "email": "not-an-email"}
    assert client.post(BASE, json=bad_email).status_code == 422


def test_children_require_a_candidate(client: TestClient) -> None:
    assert client.post(f"{BASE}/skills", json={"name": "x"}).status_code == 404
    assert client.get(f"{BASE}/skills").status_code == 404


# --- Facts ---------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "payload"), FACT_ENDPOINTS)
def test_fact_creation_and_listing(
    client: TestClient, created_candidate: dict[str, Any], path: str, payload: dict[str, Any]
) -> None:
    assert client.get(f"{BASE}/{path}").json() == []

    created = post_ok(client, path, payload)

    assert created["candidate_id"] == created_candidate["id"]
    assert created["state"] == "unknown"
    assert created["evidence_ids"] == []
    assert [item["id"] for item in client.get(f"{BASE}/{path}").json()] == [created["id"]]


def test_skill_level_is_never_inferred(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    skill = post_ok(client, "skills", {"name": "Fixture Skill"})

    assert skill["level"] is None


def test_duplicate_skill_is_a_conflict(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    post_ok(client, "skills", {"name": "Fixture Skill"})

    assert client.post(f"{BASE}/skills", json={"name": "Fixture Skill"}).status_code == 409


def test_date_ranges_are_validated(client: TestClient, created_candidate: dict[str, Any]) -> None:
    response = client.post(
        f"{BASE}/education",
        json={"institution": "i", "start_date": "2024-01-01", "end_date": "2023-01-01"},
    )

    assert response.status_code == 422


def test_project_urls_must_be_http(client: TestClient, created_candidate: dict[str, Any]) -> None:
    response = client.post(f"{BASE}/projects", json={"name": "p", "url": "javascript:alert(1)"})

    assert response.status_code == 422


# --- Preferences & constraints -------------------------------------------------------


def test_preferences_are_separate_from_skills(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    assert client.get(f"{BASE}/preferences").status_code == 404

    created = post_ok(
        client,
        "preferences",
        {
            "target_domains": ["Data/IA"],
            "contract_types": ["apprenticeship"],
            "remote_preference": "hybrid",
            "minimum_salary": 1000,
            "salary_currency": "EUR",
        },
    )

    assert created["target_domains"] == ["Data/IA"]
    assert client.get(f"{BASE}/preferences").json()["contract_types"] == ["apprenticeship"]
    assert client.get(f"{BASE}/skills").json() == []
    assert "state" not in created and "evidence_ids" not in created


def test_preferences_can_only_be_created_once(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    post_ok(client, "preferences", {})

    assert client.post(f"{BASE}/preferences", json={}).status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"minimum_salary": 2000, "preferred_salary": 1000, "salary_currency": "EUR"},
        {"minimum_salary": 1000},
        {"contract_types": ["not-a-contract"]},
    ],
)
def test_invalid_preferences_are_rejected(
    client: TestClient, created_candidate: dict[str, Any], payload: dict[str, Any]
) -> None:
    assert client.post(f"{BASE}/preferences", json=payload).status_code == 422


def test_constraints(client: TestClient, created_candidate: dict[str, Any]) -> None:
    created = post_ok(
        client,
        "constraints",
        {"constraint_type": "geographic", "description": "fixture constraint", "value": {"km": 30}},
    )

    assert created["is_hard"] is True
    assert client.get(f"{BASE}/constraints").json()[0]["value"] == {"km": 30}


# --- Evidence ------------------------------------------------------------------------


def evidence_payload(**overrides: Any) -> dict[str, Any]:
    return {"source_type": "cv", "source_name": "fixture cv", **overrides}


def test_evidence_defaults_and_listing(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    created = post_ok(client, "evidence", evidence_payload(source_uri="profile/fixture.pdf"))

    assert created["verified"] is False
    assert created["confidence"] == "low"
    assert created["links"] == []
    assert client.get(f"{BASE}/evidence").json()[0]["source_uri"] == "profile/fixture.pdf"


@pytest.mark.parametrize(
    "uri",
    ["/etc/passwd", "C:\\Users\\someone\\cv.pdf", "../outside.pdf", "~/cv.pdf", "ftp://host/x"],
)
def test_evidence_source_uri_must_be_url_or_relative_path(
    client: TestClient, created_candidate: dict[str, Any], uri: str
) -> None:
    response = client.post(f"{BASE}/evidence", json=evidence_payload(source_uri=uri))

    assert response.status_code == 422


def test_evidence_makes_a_skill_traceable(
    client: TestClient, created_candidate: dict[str, Any]
) -> None:
    skill = post_ok(client, "skills", {"name": "Fixture Skill"})
    project = post_ok(client, "projects", {"name": "Fixture Project"})
    repo_evidence = post_ok(
        client, "evidence", evidence_payload(source_type="github", confidence="high")
    )
    cv_evidence = post_ok(client, "evidence", evidence_payload(verified=True))

    for evidence, target_type, target in [
        (repo_evidence, "skill", skill),
        (cv_evidence, "skill", skill),
        (repo_evidence, "project", project),
    ]:
        link = post_ok(
            client,
            "evidence-links",
            {"evidence_id": evidence["id"], "target_type": target_type, "target_id": target["id"]},
        )
        assert link["target_type"] == target_type and link["target_id"] == target["id"]

    skills = client.get(f"{BASE}/skills").json()
    assert skills[0]["state"] == "verified"
    assert skills[0]["evidence_ids"] == sorted([repo_evidence["id"], cv_evidence["id"]])
    assert client.get(f"{BASE}/projects").json()[0]["state"] == "known"
    listed = {item["id"]: item for item in client.get(f"{BASE}/evidence").json()}
    assert len(listed[repo_evidence["id"]]["links"]) == 2


def test_evidence_link_errors(client: TestClient, created_candidate: dict[str, Any]) -> None:
    skill = post_ok(client, "skills", {"name": "Fixture Skill"})
    evidence = post_ok(client, "evidence", evidence_payload())
    body = {"evidence_id": evidence["id"], "target_type": "skill", "target_id": skill["id"]}

    assert client.post(f"{BASE}/evidence-links", json={**body, "target_id": 999}).status_code == 404
    assert (
        client.post(f"{BASE}/evidence-links", json={**body, "evidence_id": 999}).status_code == 404
    )
    assert (
        client.post(f"{BASE}/evidence-links", json={**body, "target_type": "x"}).status_code == 422
    )
    assert client.post(f"{BASE}/evidence-links", json=body).status_code == 201
    assert client.post(f"{BASE}/evidence-links", json=body).status_code == 409
