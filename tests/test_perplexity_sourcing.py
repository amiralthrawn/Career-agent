"""PerplexityWebSearchProvider: HTTP transport is a fake (no socket, no real network, no real key).

Focus: a raw search result NEVER gets a `company_name`/`offer_title` attribute unless the model's
own narrative explicitly states one, referencing one of THIS call's own result URLs (see
app/integrations/sourcing/perplexity.py).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.core.secrets import PERPLEXITY_API_KEY, InMemorySecretStore
from app.integrations.sourcing.perplexity import (
    DEFAULT_TIMEOUT_SECONDS,
    PerplexityWebSearchProvider,
)
from app.integrations.sourcing.ports import (
    ProviderError,
    ProviderErrorCode,
    QueryCriterion,
    SearchQuery,
)
from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    EmploymentType,
    SourcingMode,
)

SECRET_VALUE = "pplx-fixture-not-a-real-key"  # noqa: S105 - synthetic, never a real key
RESULT_URL = "https://example-corp.invalid/careers/data-analyst"


@dataclass
class FakeResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass
class FakeConnection:
    status: int = 200
    body: bytes = b"{}"
    raises: Exception | None = None
    calls: list[tuple[str, str, bytes, dict[str, str]]] = field(default_factory=list)
    closed: bool = False

    def request(self, method: str, url: str, body: bytes, headers: Mapping[str, str]) -> None:
        self.calls.append((method, url, body, dict(headers)))
        if self.raises is not None:
            raise self.raises

    def getresponse(self) -> FakeResponse:
        return FakeResponse(self.status, self.body)

    def close(self) -> None:
        self.closed = True


def secrets_with_key(value: str = SECRET_VALUE) -> InMemorySecretStore:
    store = InMemorySecretStore()
    store.set(PERPLEXITY_API_KEY, value)
    return store


def agent_response(
    narrative: str = "", results: list[dict[str, object]] | None = None, status: str = "completed"
) -> bytes:
    return json.dumps(
        {
            "status": status,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": narrative}],
                },
                {
                    "type": "search_results",
                    "results": (
                        results
                        if results is not None
                        else [
                            {
                                "url": RESULT_URL,
                                "title": "Data Analyst - Example Corp",
                                "snippet": "Join our team.",
                                "date": "2026-01-15",
                            }
                        ]
                    ),
                },
            ],
        }
    ).encode()


def provider(connection: FakeConnection, **kwargs: object) -> PerplexityWebSearchProvider:
    return PerplexityWebSearchProvider(
        secrets_with_key(),
        "low",
        connect=lambda: connection,
        **kwargs,  # type: ignore[arg-type]
    )


def query(mode: SourcingMode = SourcingMode.OFFERS, **overrides: object) -> SearchQuery:
    defaults: dict[str, object] = {
        "mode": mode,
        "profile_id": 1,
        "criteria": (
            QueryCriterion(
                CriterionDimension.KEYWORD,
                CriterionOperator.ANY_OF,
                ("data analyst",),
                CriterionLevel.REQUIRED,
            ),
        ),
        "max_results": 10,
    }
    return SearchQuery(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- Stated identity, trusted or dropped --------------------------------------------------------


def test_a_stated_company_and_offer_are_reported_as_attributes() -> None:
    body = agent_response(
        narrative=(
            "I found one relevant result.\n"
            f"COMPANY: {RESULT_URL} => Example Corp\n"
            f"OFFER: {RESULT_URL} => Data Analyst Intern\n"
        )
    )

    result = provider(FakeConnection(status=200, body=body)).search(query())

    assert result.completed is True
    (hit,) = result.hits
    assert hit.provider == "perplexity" and hit.url == RESULT_URL
    assert hit.attributes == {"company_name": "Example Corp", "offer_title": "Data Analyst Intern"}


def test_a_hit_with_no_stated_identity_carries_no_attribute() -> None:
    body = agent_response(narrative="One possibly relevant result, but I cannot confirm who.")

    (hit,) = provider(FakeConnection(status=200, body=body)).search(query()).hits

    assert hit.attributes == {}


def test_a_company_line_referencing_an_unknown_url_is_ignored_not_an_error() -> None:
    body = agent_response(narrative="COMPANY: https://not-one-of-the-results.invalid/x => Ghost Co")

    result = provider(FakeConnection(status=200, body=body)).search(query())

    (hit,) = result.hits
    assert hit.attributes == {}
    assert result.completed is True  # never an error: just nothing trusted


def test_a_malformed_company_line_is_ignored() -> None:
    body = agent_response(narrative=f"COMPANY {RESULT_URL} Example Corp\n")  # missing "=>" shape

    (hit,) = provider(FakeConnection(status=200, body=body)).search(query()).hits

    assert hit.attributes == {}


def test_only_company_is_stated_offer_title_stays_absent() -> None:
    body = agent_response(narrative=f"COMPANY: {RESULT_URL} => Example Corp\n")

    (hit,) = provider(FakeConnection(status=200, body=body)).search(query()).hits

    assert hit.attributes == {"company_name": "Example Corp"}


# --- Task construction (no candidate data, mode-appropriate) --------------------------------


def test_the_task_carries_the_profiles_own_criteria() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    q = query(
        criteria=(
            QueryCriterion(
                CriterionDimension.LOCATION,
                CriterionOperator.ANY_OF,
                ("Paris",),
                CriterionLevel.PREFERRED,
            ),
            QueryCriterion(
                CriterionDimension.SECTOR,
                CriterionOperator.ANY_OF,
                ("energy",),
                CriterionLevel.PREFERRED,
            ),
        )
    )

    provider(connection).search(q)

    sent = json.loads(connection.calls[0][2])
    assert "Paris" in sent["input"] and "energy" in sent["input"]
    assert "PURPOSE" in sent["instructions"] and "BOUNDARIES" in sent["instructions"]
    assert sent["preset"] == "low" and sent["store"] is False
    assert sent["tools"] == [{"type": "web_search", "max_results": 10}]


def test_offers_mode_mentions_a_single_contract_type_companies_mode_never_does() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    offers_query = query(mode=SourcingMode.OFFERS, contract_type=EmploymentType.APPRENTICESHIP)
    companies_query = query(
        mode=SourcingMode.COMPANIES, contract_type=EmploymentType.APPRENTICESHIP
    )

    provider(connection).search(offers_query)
    provider(connection).search(companies_query)

    offers_sent, companies_sent = (json.loads(c[2]) for c in connection.calls)
    assert "apprenticeship" in offers_sent["input"]
    assert "apprenticeship" not in companies_sent["input"]
    assert "SPONTANEOUS" in companies_sent["input"]


def test_a_present_key_is_sent_as_a_bearer_token() -> None:
    connection = FakeConnection(status=200, body=agent_response())

    provider(connection).search(query())

    _, _, _, headers = connection.calls[0]
    assert headers["Authorization"] == f"Bearer {SECRET_VALUE}"


# --- Errors --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, ProviderErrorCode.OTHER),
        (401, ProviderErrorCode.UNAUTHORIZED),
        (403, ProviderErrorCode.UNAUTHORIZED),
        (429, ProviderErrorCode.RATE_LIMITED),
        (500, ProviderErrorCode.UNAVAILABLE),
    ],
)
def test_each_http_status_maps_to_a_stable_code(status: int, code: ProviderErrorCode) -> None:
    connection = FakeConnection(status=status, body=b'{"error": "server message"}')

    with pytest.raises(ProviderError) as excinfo:
        provider(connection).search(query())

    assert excinfo.value.code is code
    assert "server message" not in str(excinfo.value)


def test_a_run_level_failure_is_reported_never_treated_as_success() -> None:
    body = agent_response(status="failed")

    with pytest.raises(ProviderError) as excinfo:
        provider(FakeConnection(status=200, body=body)).search(query())

    assert excinfo.value.code is ProviderErrorCode.OTHER


def test_a_timeout_and_a_network_error_are_reported_distinctly() -> None:
    with pytest.raises(ProviderError) as timeout_info:
        provider(FakeConnection(raises=TimeoutError())).search(query())
    with pytest.raises(ProviderError) as network_info:
        provider(FakeConnection(raises=ConnectionResetError("reset"))).search(query())

    assert timeout_info.value.code is ProviderErrorCode.TIMEOUT
    assert network_info.value.code is ProviderErrorCode.UNAVAILABLE


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"output": "not a list"}'])
def test_a_malformed_response_is_invalid_response_never_a_fabricated_result(body: bytes) -> None:
    with pytest.raises(ProviderError) as excinfo:
        provider(FakeConnection(status=200, body=body)).search(query())

    assert excinfo.value.code is ProviderErrorCode.INVALID_RESPONSE


def test_an_empty_output_is_a_valid_completed_search_with_no_hits() -> None:
    """Distinct from a malformed response: the provider completed, it just found nothing."""
    result = provider(FakeConnection(status=200, body=b'{"output": []}')).search(query())

    assert result.completed is True and result.hits == ()


def test_a_search_result_without_a_url_is_dropped_not_invented() -> None:
    body = agent_response(results=[{"title": "No URL here", "snippet": "..."}])

    result = provider(FakeConnection(status=200, body=body)).search(query())

    assert result.hits == ()


def test_hits_are_bounded_by_max_results() -> None:
    results = [
        {"url": f"https://example-{n}.invalid", "title": f"Result {n}", "snippet": "..."}
        for n in range(5)
    ]
    body = agent_response(results=results)

    result = provider(FakeConnection(status=200, body=body)).search(query(max_results=2))

    assert len(result.hits) == 2


def test_sources_consulted_are_the_result_domains() -> None:
    body = agent_response(
        results=[
            {"url": "https://a.example.invalid/1", "title": "A"},
            {"url": "https://b.example.invalid/2", "title": "B"},
            {"url": "https://a.example.invalid/3", "title": "A again"},
        ]
    )

    result = provider(FakeConnection(status=200, body=body)).search(query())

    assert result.sources_consulted == ("a.example.invalid", "b.example.invalid")


# --- Configuration / security ------------------------------------------------------------------


def test_a_missing_key_fails_closed_before_any_request_is_attempted() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    empty = PerplexityWebSearchProvider(InMemorySecretStore(), "low", connect=lambda: connection)

    with pytest.raises(ProviderError) as excinfo:
        empty.search(query())

    assert excinfo.value.code is ProviderErrorCode.UNAUTHORIZED
    assert connection.calls == []


def test_the_timeout_is_configurable_and_reaches_the_real_connection_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingConnection:
        def __init__(self, host: str, timeout: float) -> None:
            captured["host"] = host
            captured["timeout"] = timeout

    monkeypatch.setattr("app.integrations.sourcing.perplexity.HTTPSConnection", RecordingConnection)
    default_provider = PerplexityWebSearchProvider(secrets_with_key(), "low")
    custom_provider = PerplexityWebSearchProvider(secrets_with_key(), "low", timeout=10.0)

    default_provider._connect()
    assert captured == {"host": "api.perplexity.ai", "timeout": DEFAULT_TIMEOUT_SECONDS}
    custom_provider._connect()
    assert captured["timeout"] == 10.0


@pytest.mark.parametrize(
    "connection",
    [
        FakeConnection(status=401, body=b"{}"),
        FakeConnection(raises=TimeoutError()),
        FakeConnection(status=200, body=b"garbage"),
    ],
)
def test_no_exception_ever_carries_the_key_or_the_raw_response(connection: FakeConnection) -> None:
    with pytest.raises(ProviderError) as excinfo:
        provider(connection).search(query())

    text = str(excinfo.value)
    assert SECRET_VALUE not in text
    assert text in {code.value for code in ProviderErrorCode}


def test_the_key_is_never_stored_on_the_provider_instance() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    instance = provider(connection)

    assert not any(
        isinstance(value, str) and SECRET_VALUE in value for value in vars(instance).values()
    )


def test_the_adapter_imports_no_network_or_ai_client_library() -> None:
    from pathlib import Path

    source = Path("app/integrations/sourcing/perplexity.py").read_text(encoding="utf-8")

    for forbidden in ("import requests", "import openai", "import anthropic", "import socket"):
        assert forbidden not in source
