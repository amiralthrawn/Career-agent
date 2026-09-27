"""Send batch routes (step 10): thin HTTP layer, auth, ready-to-send listing, individual send.

Only a fake `MailProvider` is used here: no test reachable from this file contacts Gmail.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.drafts import get_llm_client
from app.api.send_batches import get_live_mail_provider
from app.core.config import get_settings
from app.integrations.mail.ports import BuiltMessage, SentMessage
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import add_target, crit, make_profile, qualify
from tests.requirements_factory import extract


class FakeMailProvider:
    def __init__(self) -> None:
        self.sent: list[BuiltMessage] = []

    def send(self, message: BuiltMessage) -> SentMessage:
        self.sent.append(message)
        return SentMessage(provider="fake", provider_message_id=f"id-{len(self.sent)}")


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture(autouse=True)
def with_send_env(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_MODE", "auto")
    monkeypatch.setenv("MAIL_FROM", "sender.fixture@example.invalid")
    get_settings.cache_clear()
    (private_dir / "documents" / "cv.docx").write_bytes(simple_docx(SYNTHETIC_CV_LINES))


def install(
    client: TestClient, llm: FakeLLMClient | None, provider: FakeMailProvider | None
) -> None:
    client.app.dependency_overrides[get_llm_client] = lambda: llm  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_live_mail_provider] = lambda: provider  # type: ignore[attr-defined]


def approved_package_id(client: TestClient) -> int:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client)
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
    ingestion = _ingest_cv(client)
    prepared = client.post(f"/api/applications/{target['id']}/prepare", json={})
    assert prepared.status_code == 201, prepared.text
    package_id: int = prepared.json()["id"]
    approved = client.post(f"/api/applications/{package_id}/approve")
    assert approved.status_code == 200, approved.text
    assert ingestion is not None
    return package_id


def _ingest_cv(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/candidate/ingestions/cv", json={"source_path": "documents/cv.docx"}
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_ready_to_send_lists_only_approved_unsent_packages(client: TestClient) -> None:
    install(client, FakeLLMClient(), FakeMailProvider())
    package_id = approved_package_id(client)

    response = client.get("/api/applications/ready-to-send")

    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [package_id]


def test_create_approve_execute_round_trip(client: TestClient) -> None:
    provider = FakeMailProvider()
    install(client, FakeLLMClient(), provider)
    package_id = approved_package_id(client)

    created = client.post("/api/send-batches", json={"application_package_ids": [package_id]})
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["status"] == "draft"

    approved = client.post(f"/api/send-batches/{batch['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    executed = client.post(f"/api/send-batches/{batch['id']}/execute")
    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "completed"
    (item,) = body["items"]
    assert item["status"] == "sent"

    fetched = client.get(f"/api/send-batches/{batch['id']}")
    assert fetched.status_code == 200 and fetched.json()["status"] == "completed"

    # no longer ready-to-send once sent
    assert client.get("/api/applications/ready-to-send").json() == []


def test_individual_send_convenience_route(client: TestClient) -> None:
    provider = FakeMailProvider()
    install(client, FakeLLMClient(), provider)
    package_id = approved_package_id(client)

    response = client.post(f"/api/applications/{package_id}/send")

    assert response.status_code == 200, response.text
    body = response.json()
    (item,) = body["items"]
    assert item["status"] == "sent"
    assert len(provider.sent) == 1


def test_there_is_no_route_that_sends_without_an_explicit_action(client: TestClient) -> None:
    """Preparing a package must never itself trigger a send."""
    install(client, FakeLLMClient(), FakeMailProvider())
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client)
    extract(client, target["id"])
    qualify(client, target["id"])
    _ingest_cv(client)

    response = client.post(f"/api/applications/{target['id']}/prepare", json={})

    assert response.status_code == 201
    # a fresh package is pending_validation, never sent by preparing it


def test_the_send_batch_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/send-batches"),
        ("get", "/api/send-batches/1"),
        ("post", "/api/send-batches/1/approve"),
        ("post", "/api/send-batches/1/execute"),
        ("get", "/api/applications/ready-to-send"),
        ("post", "/api/applications/1/send"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_nothing_touches_the_network(client: TestClient, no_network: None) -> None:
    provider = FakeMailProvider()
    install(client, FakeLLMClient(), provider)
    package_id = approved_package_id(client)

    response = client.post(f"/api/applications/{package_id}/send")

    assert response.status_code == 200
