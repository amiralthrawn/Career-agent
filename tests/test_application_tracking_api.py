"""Application lifecycle tracking routes (step 11): thin HTTP layer, auth, manual entry,
correction, filtering. No test reachable from this file sends anything or touches Gmail for real.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import add_target, crit, make_profile, qualify
from tests.requirements_factory import extract


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture(autouse=True)
def with_env(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_MODE", "dry_run")
    monkeypatch.setenv("MAIL_FROM", "sender.fixture@example.invalid")
    get_settings.cache_clear()


def install_llm(client: TestClient) -> None:
    from app.api.drafts import get_llm_client

    client.app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()  # type: ignore[attr-defined]


def approved_package_id(client: TestClient, *, domain: str = "a.invalid", name: str = "A") -> int:
    install_llm(client)
    make_profile(
        client, [crit("contract_type", ["apprenticeship"], "required")], name=f"profile-{domain}"
    )
    target = add_target(client, domain=domain, name=name)
    extract(client, target["id"])
    assert qualify(client, target["id"]).status_code in (200, 201)
    client.post(
        f"/api/targets/{target['id']}/contacts",
        json={
            "contact": {
                "full_name": "Jamie Fixture",
                "role_category": "recruiter",
                "channels": [
                    {
                        "kind": "email",
                        "value": "jamie@fixture-corp.example.invalid",
                        "source": {"kind": "manual", "label": "test", "reference": "note"},
                    }
                ],
            }
        },
    )
    from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx

    private_dir = get_settings().private_data_path
    (private_dir / "documents" / "cv.docx").write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    ingestion = client.post(
        "/api/candidate/ingestions/cv", json={"source_path": "documents/cv.docx"}
    )
    # The same CV file is reused across several packages in one test: already-ingested (409) is
    # fine, the existing DocumentIngestion is still usable; anything else is a real failure.
    assert ingestion.status_code in (201, 409), ingestion.text
    prepared = client.post(f"/api/applications/{target['id']}/prepare", json={})
    assert prepared.status_code == 201, prepared.text
    package_id: int = prepared.json()["id"]
    approved = client.post(f"/api/applications/{package_id}/approve")
    assert approved.status_code == 200, approved.text
    return package_id


def test_prepared_and_approved_events_are_already_present(client: TestClient) -> None:
    package_id = approved_package_id(client)

    response = client.get(f"/api/applications/{package_id}/events")

    assert response.status_code == 200
    types = {e["event_type"] for e in response.json()}
    assert types == {"prepared", "approved"}
    assert all(e["origin"] == "system" and e["status"] == "found" for e in response.json())


def test_manual_event_round_trip(client: TestClient) -> None:
    package_id = approved_package_id(client)

    created = client.post(
        f"/api/applications/{package_id}/events",
        json={
            "event_type": "rejected",
            "occurred_at": "2026-11-03T10:00:00Z",
            "note": "Received a rejection e-mail",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["origin"] == "manual" and body["status"] == "found"

    listed = client.get(f"/api/applications/{package_id}/events").json()
    assert any(e["id"] == body["id"] for e in listed)


@pytest.mark.parametrize("event_type", ["prepared", "approved", "sent"])
def test_manual_entry_refuses_system_only_types(client: TestClient, event_type: str) -> None:
    package_id = approved_package_id(client)

    response = client.post(
        f"/api/applications/{package_id}/events",
        json={"event_type": event_type, "occurred_at": "2026-11-03T10:00:00Z"},
    )

    assert response.status_code == 422


def test_correcting_an_event_creates_a_new_one_and_keeps_the_original(client: TestClient) -> None:
    package_id = approved_package_id(client)
    original = client.post(
        f"/api/applications/{package_id}/events",
        json={"event_type": "rejected", "occurred_at": "2026-11-03T10:00:00Z", "note": "typo"},
    ).json()

    corrected = client.post(
        f"/api/applications/{package_id}/events/{original['id']}/correct",
        json={"event_type": "response_received"},
    )

    assert corrected.status_code == 200, corrected.text
    body = corrected.json()
    assert body["corrected_event_id"] == original["id"]

    listed = client.get(f"/api/applications/{package_id}/events").json()
    kept = next(e for e in listed if e["id"] == original["id"])
    assert kept["event_type"] == "rejected"  # never silently overwritten


def test_correction_requires_at_least_one_field(client: TestClient) -> None:
    package_id = approved_package_id(client)
    original = client.post(
        f"/api/applications/{package_id}/events",
        json={"event_type": "rejected", "occurred_at": "2026-11-03T10:00:00Z"},
    ).json()

    response = client.post(
        f"/api/applications/{package_id}/events/{original['id']}/correct", json={}
    )

    assert response.status_code == 422


def test_list_applications_filters_by_event_type(client: TestClient) -> None:
    rejected_id = approved_package_id(client, domain="rejected.invalid", name="Rejected Co")
    approved_package_id(client, domain="other.invalid", name="Other Co")
    client.post(
        f"/api/applications/{rejected_id}/events",
        json={"event_type": "rejected", "occurred_at": "2026-11-03T10:00:00Z"},
    )

    response = client.get("/api/applications", params={"event_type": "rejected"})

    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [rejected_id]


def test_needing_follow_up_route_is_reachable(client: TestClient) -> None:
    response = client.get("/api/applications/needing-follow-up", params={"days": 14})

    assert response.status_code == 200
    assert response.json() == []  # nothing sent yet in this test


def test_the_tracking_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("get", "/api/applications"),
        ("get", "/api/applications/needing-follow-up"),
        ("get", "/api/applications/1/events"),
        ("post", "/api/applications/1/events"),
        ("post", "/api/applications/1/events/1/correct"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_no_send_or_follow_up_route_sends_anything(client: TestClient, no_network: None) -> None:
    package_id = approved_package_id(client)

    response = client.post(
        f"/api/applications/{package_id}/events",
        json={"event_type": "follow_up_sent", "occurred_at": "2026-11-03T10:00:00Z"},
    )

    assert response.status_code == 201  # recording that a human already sent one - not sending
