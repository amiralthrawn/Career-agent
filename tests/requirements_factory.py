"""Synthetic builders for the requirement tests (API level). Everything is fictional."""

from typing import Any

from fastapi.testclient import TestClient

from tests.qualification_factory import add_target, crit, make_profile, qualify

OFFER_TEXT = "Python is required.\nSQL is a plus.\nPower BI is a plus.\n2 years of experience.\n"

MANUAL_SOURCE = {"kind": "manual", "label": "Recruiter call", "reference": "call of the 3rd"}


def active_profile(client: TestClient) -> dict[str, Any]:
    """A profile the fixture targets satisfy: qualification then says `candidate`."""
    return make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])


def offer_target(client: TestClient, text: str = OFFER_TEXT, **kwargs: Any) -> dict[str, Any]:
    return add_target(client, opportunity={"description_text": text}, **kwargs)


def spontaneous_target(client: TestClient) -> dict[str, Any]:
    return add_target(client, offer=False)


def add_evidence(client: TestClient, *, confidence: str = "medium", verified: bool = False) -> int:
    response = client.post(
        "/api/candidate/evidence",
        json={
            "source_type": "document",
            "source_name": "Synthetic fixture document",
            "confidence": confidence,
            "verified": verified,
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def give_state(client: TestClient, target_type: str, target_id: int, state: str) -> None:
    """Attach evidence so that the fact ends up in the requested (derived) state."""
    if state == "unknown":
        return  # no evidence at all
    evidence_id = add_evidence(
        client,
        confidence="medium" if state == "known" else "low",
        verified=state == "verified",
    )
    response = client.post(
        "/api/candidate/evidence-links",
        json={"evidence_id": evidence_id, "target_type": target_type, "target_id": target_id},
    )
    assert response.status_code == 201, response.text


def add_skill(client: TestClient, name: str, state: str = "known") -> dict[str, Any]:
    response = client.post("/api/candidate/skills", json={"name": name})
    assert response.status_code == 201, response.text
    give_state(client, "skill", response.json()["id"], state)
    result: dict[str, Any] = response.json()
    return result


def add_project(
    client: TestClient, name: str, description: str | None = None, state: str = "known"
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if description:
        body["description"] = description
    response = client.post("/api/candidate/projects", json=body)
    assert response.status_code == 201, response.text
    give_state(client, "project", response.json()["id"], state)
    result: dict[str, Any] = response.json()
    return result


def add_experience(
    client: TestClient,
    title: str = "Analyst",
    start: str | None = "2022-01-01",
    end: str | None = "2024-06-01",
    state: str = "known",
    description: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"company": "Fixture Employer", "title": title}
    if start:
        body["start_date"] = start
    if end:
        body["end_date"] = end
    if description:
        body["description"] = description
    response = client.post("/api/candidate/experiences", json=body)
    assert response.status_code == 201, response.text
    give_state(client, "experience", response.json()["id"], state)
    result: dict[str, Any] = response.json()
    return result


def extract(client: TestClient, target_id: int) -> dict[str, Any]:
    response = client.post(f"/api/targets/{target_id}/requirements/extract")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def requirements(
    client: TestClient, target_id: int, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/targets/{target_id}/requirements", params={"include_inactive": include_inactive}
    )
    assert response.status_code == 200, response.text
    result: list[dict[str, Any]] = response.json()
    return result


def by_key(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in items}


def manual_body(**overrides: Any) -> dict[str, Any]:
    return {
        "label": "Docker",
        "importance": "required",
        "excerpt": "The recruiter said Docker is mandatory.",
        "source": MANUAL_SOURCE,
        **overrides,
    }


def scenario(client: TestClient) -> dict[str, Any]:
    """A qualified target with a mix of covered / weak / gap requirements (used by 3b and 4 tests).

    The offer says: Python required, SQL a plus, Power BI a plus, "2 years of experience". The
    Candidate Brain holds: Skill Python (known), Skill SQL (uncertain), a Project that mentions
    Power BI and Python (known) but NO Skill Power BI, and one dated experience of 2 years and
    5 months (known).
    """
    active_profile(client)
    target = offer_target(client)
    extract(client, target["id"])
    add_skill(client, "Python", "known")
    add_skill(client, "SQL", "uncertain")
    add_project(client, "Dashboard", "Built a dashboard with Power BI and Python", "known")
    add_experience(client, "Analyst", "2022-01-01", "2024-06-01", "known")
    assert qualify(client, target["id"]).status_code == 201
    return target


def brief(client: TestClient, target_id: int) -> dict[str, Any]:
    response = client.get(f"/api/targets/{target_id}/personalization-brief")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result
