"""PerplexityClient: HTTP transport is a fake (no socket, no real network, no real key)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.core.secrets import PERPLEXITY_API_KEY, InMemorySecretStore
from app.integrations.research.perplexity import DEFAULT_TIMEOUT_SECONDS, PerplexityClient
from app.integrations.research.ports import (
    ResearchError,
    ResearchErrorCode,
    ResearchQuery,
    ResearchStatus,
    ResearchSubject,
)

SECRET_VALUE = "pplx-fixture-not-a-real-key"  # noqa: S105 - a synthetic value, never used


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
    text: str = "Example Data SAS is a fictional software company.",
    model: str = "openai/gpt-6-luna",
    results: list[dict[str, object]] | None = None,
    input_tokens: int = 30,
    output_tokens: int = 12,
) -> bytes:
    return json.dumps(
        {
            "id": "resp_fixture",
            "object": "response",
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
                {
                    "type": "search_results",
                    "queries": ["Example Data SAS"],
                    "results": results
                    if results is not None
                    else [
                        {
                            "id": 1,
                            "url": "https://example-data.invalid/about",
                            "title": "About Example Data SAS",
                            "snippet": "A fictional software company based in Paris.",
                            "date": "2026-01-15",
                        }
                    ],
                },
            ],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    ).encode()


def client(connection: FakeConnection, **kwargs: object) -> PerplexityClient:
    return PerplexityClient(
        secrets_with_key(),
        "low",
        connect=lambda: connection,
        **kwargs,  # type: ignore[arg-type]
    )


def query(**overrides: object) -> ResearchQuery:
    defaults: dict[str, object] = {
        "objective": "Understand the company's technology environment",
        "subject": ResearchSubject(company_name="Example Data SAS"),
    }
    return ResearchQuery(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- Success -------------------------------------------------------------------------------


def test_a_successful_call_returns_sourced_observations_and_a_separate_summary() -> None:
    connection = FakeConnection(status=200, body=agent_response())

    result = client(connection).research(query())

    assert result.provider == "perplexity" and result.model == "openai/gpt-6-luna"
    assert result.status is ResearchStatus.OK
    assert result.summary == "Example Data SAS is a fictional software company."
    (obs,) = result.observations
    assert obs.source_url == "https://example-data.invalid/about"
    assert obs.source_title == "About Example Data SAS"
    assert obs.excerpt == "A fictional software company based in Paris."
    assert obs.published == "2026-01-15"
    assert result.usage is not None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (30, 12)
    assert isinstance(result.duration_ms, int) and result.duration_ms >= 0
    assert connection.closed is True


def test_no_search_results_but_a_summary_is_still_a_usable_result() -> None:
    body = agent_response(results=[])

    result = client(FakeConnection(status=200, body=body)).research(query())

    assert result.status is ResearchStatus.NO_RESULTS
    assert result.observations == ()
    assert result.summary


def test_the_request_carries_both_the_contract_and_the_task_layers_separately() -> None:
    connection = FakeConnection(status=200, body=agent_response())

    client(connection).research(
        query(objective="Find recent news", focus_areas=("recent news", "hiring signals"))
    )

    sent = json.loads(connection.calls[0][2])
    assert "PURPOSE" in sent["instructions"] and "BOUNDARIES" in sent["instructions"]
    assert "do not qualify targets" in sent["instructions"]
    assert "Example Data SAS" in sent["input"] and "Find recent news" in sent["input"]
    assert "recent news" in sent["input"] and "hiring signals" in sent["input"]
    assert sent["preset"] == "low" and sent["store"] is False
    assert sent["tools"] == [{"type": "web_search", "max_results": 10}]


def test_a_present_key_is_sent_as_a_bearer_token() -> None:
    connection = FakeConnection(status=200, body=agent_response())

    client(connection).research(query())

    _, _, _, headers = connection.calls[0]
    assert headers["Authorization"] == f"Bearer {SECRET_VALUE}"


# --- Errors --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, ResearchErrorCode.OTHER),
        (401, ResearchErrorCode.UNAUTHORIZED),
        (403, ResearchErrorCode.UNAUTHORIZED),
        (429, ResearchErrorCode.RATE_LIMITED),
        (500, ResearchErrorCode.UNAVAILABLE),
        (503, ResearchErrorCode.UNAVAILABLE),
    ],
)
def test_each_http_status_maps_to_a_stable_non_sensitive_code(
    status: int, code: ResearchErrorCode
) -> None:
    connection = FakeConnection(status=status, body=b'{"error": "server message"}')

    with pytest.raises(ResearchError) as excinfo:
        client(connection).research(query())

    assert excinfo.value.code is code
    assert "server message" not in str(excinfo.value)


def test_a_run_level_failure_is_reported_never_treated_as_success() -> None:
    body = json.dumps({"status": "failed", "error": {"message": "internal", "code": "x"}}).encode()

    with pytest.raises(ResearchError) as excinfo:
        client(FakeConnection(status=200, body=body)).research(query())

    assert excinfo.value.code is ResearchErrorCode.OTHER


def test_a_timeout_and_a_network_error_are_reported_distinctly() -> None:
    with pytest.raises(ResearchError) as timeout_info:
        client(FakeConnection(raises=TimeoutError())).research(query())
    with pytest.raises(ResearchError) as network_info:
        client(FakeConnection(raises=ConnectionResetError("reset"))).research(query())

    assert timeout_info.value.code is ResearchErrorCode.TIMEOUT
    assert network_info.value.code is ResearchErrorCode.UNAVAILABLE


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"output": "not a list"}',
        b'{"output": []}',  # nothing exploitable at all
        b'{"output": [{"type": "message", "content": [{"type": "output_text", "text": ""}]}]}',
    ],
)
def test_a_malformed_or_empty_response_is_invalid_response_never_a_fabricated_result(
    body: bytes,
) -> None:
    with pytest.raises(ResearchError) as excinfo:
        client(FakeConnection(status=200, body=body)).research(query())

    assert excinfo.value.code is ResearchErrorCode.INVALID_RESPONSE


def test_a_search_result_without_a_url_is_never_turned_into_an_observation() -> None:
    body = agent_response(results=[{"title": "No URL here", "snippet": "..."}])

    result = client(FakeConnection(status=200, body=body)).research(query())

    assert result.observations == ()  # provenance is mandatory: dropped, not invented


def test_observations_are_bounded_by_max_results() -> None:
    results: list[dict[str, object]] = [
        {"url": f"https://example-{n}.invalid", "title": f"Result {n}", "snippet": "..."}
        for n in range(5)
    ]
    body = agent_response(results=results)

    result = client(FakeConnection(status=200, body=body)).research(query(max_results=2))

    assert len(result.observations) == 2


# --- Configuration / security ------------------------------------------------------------------


def test_a_missing_key_fails_closed_before_any_request_is_attempted() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    empty = PerplexityClient(InMemorySecretStore(), "low", connect=lambda: connection)

    with pytest.raises(ResearchError) as excinfo:
        empty.research(query())

    assert excinfo.value.code is ResearchErrorCode.UNAUTHORIZED
    assert connection.calls == []


def test_the_timeout_is_configurable_and_reaches_the_real_connection_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingConnection:
        def __init__(self, host: str, timeout: float) -> None:
            captured["host"] = host
            captured["timeout"] = timeout

    monkeypatch.setattr("app.integrations.research.perplexity.HTTPSConnection", RecordingConnection)
    default_client = PerplexityClient(secrets_with_key(), "low")
    custom_client = PerplexityClient(secrets_with_key(), "low", timeout=10.0)

    default_client._connect()
    assert captured == {"host": "api.perplexity.ai", "timeout": DEFAULT_TIMEOUT_SECONDS}
    custom_client._connect()
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
    with pytest.raises(ResearchError) as excinfo:
        client(connection).research(query())

    text = str(excinfo.value)
    assert SECRET_VALUE not in text
    assert text in {code.value for code in ResearchErrorCode}


def test_the_key_is_never_stored_on_the_client_instance() -> None:
    connection = FakeConnection(status=200, body=agent_response())
    instance = client(connection)

    assert not any(
        isinstance(value, str) and SECRET_VALUE in value for value in vars(instance).values()
    )
