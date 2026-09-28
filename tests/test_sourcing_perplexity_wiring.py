"""Wiring of the real Perplexity adapter into sourcing (step 3c): the capability gate
(`default_web_search_provider`, same convention as company/contact research) and one end-to-end
test proving a Perplexity-shaped result flows all the way to a Target through the unchanged
`SourcingService`/`HitExtractor`. The adapter's own behaviour is covered by
tests/test_perplexity_sourcing.py; this file is deliberately small.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sourcing import get_providers
from app.core.config import Settings
from app.core.secrets import PERPLEXITY_API_KEY, InMemorySecretStore
from app.integrations.sourcing.perplexity import PerplexityWebSearchProvider
from app.integrations.sourcing.ports import SourcingProviders
from app.models import Target
from app.services.sourcing import default_web_search_provider
from tests.qualification_factory import crit, make_profile
from tests.sourcing_fakes import start

RESULT_URL = "https://example-corp.invalid/careers/data-analyst"


@dataclass
class FakeResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass
class FakeConnection:
    body: bytes
    calls: list[tuple[str, str, bytes, dict[str, str]]] = field(default_factory=list)

    def request(self, method: str, url: str, body: bytes, headers: Mapping[str, str]) -> None:
        self.calls.append((method, url, body, dict(headers)))

    def getresponse(self) -> FakeResponse:
        return FakeResponse(200, self.body)

    def close(self) -> None:
        pass


def agent_response(narrative: str, results: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": narrative}],
                },
                {"type": "search_results", "results": results},
            ],
        }
    ).encode()


# --- Capability gate (mirrors app.services.company_research's default_research_provider) --------


def test_disabled_by_default_gives_no_provider() -> None:
    settings = Settings(research_enabled=False, perplexity_preset="low")
    secrets = InMemorySecretStore()
    secrets.set(PERPLEXITY_API_KEY, "pplx-fixture")

    assert default_web_search_provider(settings, secrets) is None


def test_enabled_but_missing_preset_or_secret_still_gives_none() -> None:
    no_preset = Settings(research_enabled=True, perplexity_preset=None)
    no_secret = Settings(research_enabled=True, perplexity_preset="low")

    assert default_web_search_provider(no_preset, InMemorySecretStore()) is None
    assert default_web_search_provider(no_secret, InMemorySecretStore()) is None


def test_fully_configured_gives_a_real_provider_instance() -> None:
    secrets = InMemorySecretStore()
    secrets.set(PERPLEXITY_API_KEY, "pplx-fixture")
    settings = Settings(research_enabled=True, perplexity_preset="low")

    provider = default_web_search_provider(settings, secrets)

    assert provider is not None and type(provider).__name__ == "PerplexityWebSearchProvider"


# --- End to end: a Perplexity-shaped result becomes a real Target -------------------------------


def _install_real_adapter(client: TestClient, connection: FakeConnection) -> None:
    secrets = InMemorySecretStore()
    secrets.set(PERPLEXITY_API_KEY, "pplx-fixture")
    provider = PerplexityWebSearchProvider(secrets, "low", connect=lambda: connection)
    providers = SourcingProviders(web={"perplexity": provider})
    client.app.dependency_overrides[get_providers] = lambda: providers  # type: ignore[attr-defined]


def test_a_perplexity_result_with_a_stated_identity_becomes_a_target(
    client: TestClient, db_session: Session, candidate_payload: dict[str, Any]
) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201
    make_profile(client, [crit("keyword", ["data analyst"], "required")])
    body = agent_response(
        narrative=(
            "One relevant offer.\n"
            f"COMPANY: {RESULT_URL} => Example Corp\n"
            f"OFFER: {RESULT_URL} => Data Analyst Intern\n"
        ),
        results=[
            {
                "url": RESULT_URL,
                "title": "Data Analyst - Example Corp",
                "snippet": "Join our team.",
                "date": "2026-01-15",
            }
        ],
    )
    _install_real_adapter(client, FakeConnection(body))

    run = start(client, mode="offers", provider="perplexity").json()

    assert run["status"] == "completed" and run["targets_created"] == 1
    target = db_session.scalars(select(Target)).one()
    assert target.company.name == "Example Corp" and target.opportunity is not None
    assert target.opportunity.title == "Data Analyst Intern"


def test_a_perplexity_result_with_no_stated_identity_is_rejected_not_guessed(
    client: TestClient, candidate_payload: dict[str, Any]
) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201
    make_profile(client, [crit("keyword", ["data analyst"], "required")])
    body = agent_response(
        narrative="One possibly relevant result, but I cannot confirm the company.",
        results=[
            {
                "url": RESULT_URL,
                "title": "Data Analyst - Example Corp",
                "snippet": "Join our team.",
            }
        ],
    )
    _install_real_adapter(client, FakeConnection(body))

    run = start(client, mode="offers", provider="perplexity").json()

    assert run["targets_created"] == 0 and run["items_rejected"] == 1
    assert run["items"][0]["reason"] == "missing_company_name"
