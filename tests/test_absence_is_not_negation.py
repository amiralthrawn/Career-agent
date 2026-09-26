"""Absence of information is not negative information.

Three situations must stay distinct:
- fact + evidence     -> verified
- fact, no evidence   -> unknown (the fact exists in the Brain, nothing backs it)
- no fact at all      -> not known / absent (no row, and never a negation)
"""

from typing import Any

from fastapi import status
from fastapi.testclient import TestClient

from app.models.enums import InformationState

BASE = "/api/candidate"


def test_information_states_contain_no_negative_or_absent_value() -> None:
    assert {state.value for state in InformationState} == {
        "unknown",
        "uncertain",
        "known",
        "verified",
    }


def test_verified_unknown_and_absent_are_three_distinct_situations(
    client: TestClient, candidate_payload: dict[str, Any]
) -> None:
    client.post(BASE, json=candidate_payload)
    python = client.post(f"{BASE}/skills", json={"name": "Python"}).json()
    client.post(f"{BASE}/skills", json={"name": "Java"})
    evidence = client.post(
        f"{BASE}/evidence", json={"source_type": "cv", "source_name": "fixture", "verified": True}
    ).json()
    link = client.post(
        f"{BASE}/evidence-links",
        json={"evidence_id": evidence["id"], "target_type": "skill", "target_id": python["id"]},
    )
    assert link.status_code == status.HTTP_201_CREATED

    skills = {skill["name"]: skill for skill in client.get(f"{BASE}/skills").json()}

    # Python: fact + evidence -> verified.
    assert skills["Python"]["state"] == "verified"
    # Java: fact without evidence -> unknown; not denied, and no level is invented.
    assert skills["Java"]["state"] == "unknown"
    assert skills["Java"]["evidence_ids"] == []
    assert skills["Java"]["level"] is None
    # Rust: nothing recorded -> no row. Absence is not a state and not a negation.
    assert "Rust" not in skills
    assert set(skills) == {"Python", "Java"}
    assert all(
        skill["state"] in {"unknown", "uncertain", "known", "verified"} for skill in skills.values()
    )


def test_empty_brain_sections_mean_not_known_rather_than_none(
    client: TestClient, candidate_payload: dict[str, Any]
) -> None:
    client.post(BASE, json=candidate_payload)

    # No languages recorded: an empty list, not a claim that the candidate speaks none.
    assert client.get(f"{BASE}/languages").json() == []
    # No preferences recorded: "not defined yet", not "no preference".
    assert client.get(f"{BASE}/preferences").status_code == status.HTTP_404_NOT_FOUND
