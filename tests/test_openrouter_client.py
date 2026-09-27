"""OpenRouterClient: HTTP transport is a fake (no socket, no real network, no real key).

This is the only place in the automated suite that exercises `OpenRouterClient`'s success and
error parsing. The transport is injected (`connect=`), so nothing here ever touches a socket - a
real network call is made only by a human running `scripts/manual/openrouter_smoke_test.py`.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.core.secrets import OPENROUTER_API_KEY, InMemorySecretStore
from app.integrations.llm.openrouter import DEFAULT_TIMEOUT_SECONDS, OpenRouterClient
from app.integrations.llm.ports import GenerationRequest, LLMError
from app.models.enums import LLMCallStatus, LLMErrorCode

SECRET_VALUE = "sk-or-v1-fixture-not-a-real-key"  # noqa: S105 - a synthetic value, never used


@dataclass
class FakeResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass
class FakeConnection:
    """Records what was sent; returns a scripted response or raises a scripted transport error."""

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
    store.set(OPENROUTER_API_KEY, value)
    return store


def success_body(
    text: str = "Dear Hiring Team, ...",
    model: str = "deepseek/deepseek-v3.2",
    finish_reason: str = "stop",
    prompt_tokens: int = 42,
    completion_tokens: int = 17,
) -> bytes:
    return json.dumps(
        {
            "id": "gen-fixture",
            "model": model,
            "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
    ).encode()


def client(connection: FakeConnection, **kwargs: object) -> OpenRouterClient:
    return OpenRouterClient(
        secrets_with_key(),
        "openrouter/free",
        connect=lambda: connection,
        **kwargs,  # type: ignore[arg-type]
    )


def request(**overrides: object) -> GenerationRequest:
    defaults = {"task": "application_email", "system": "Write a draft.", "context": {"a": 1}}
    return GenerationRequest(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- Success -------------------------------------------------------------------------------


def test_a_successful_call_returns_a_conforming_generation_result() -> None:
    connection = FakeConnection(status=200, body=success_body())

    result = client(connection).generate(request())

    assert result.text == "Dear Hiring Team, ..."
    assert result.model == "deepseek/deepseek-v3.2"
    assert result.status is LLMCallStatus.OK
    assert result.usage is not None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (42, 17)
    assert connection.closed is True


def test_a_successful_call_reports_a_measured_duration() -> None:
    connection = FakeConnection(status=200, body=success_body())

    result = client(connection).generate(request())

    assert isinstance(result.duration_ms, int) and result.duration_ms >= 0


def test_a_truncated_finish_reason_is_reported_as_such() -> None:
    connection = FakeConnection(status=200, body=success_body(finish_reason="length"))

    result = client(connection).generate(request())

    assert result.status is LLMCallStatus.TRUNCATED


def test_the_default_model_is_used_when_the_request_does_not_name_one() -> None:
    connection = FakeConnection(status=200, body=success_body())

    client(connection).generate(request())

    sent = json.loads(connection.calls[0][2])
    assert sent["model"] == "openrouter/free"


def test_a_request_level_model_overrides_the_clients_default() -> None:
    connection = FakeConnection(status=200, body=success_body())

    client(connection).generate(request(model="some/other-model"))

    sent = json.loads(connection.calls[0][2])
    assert sent["model"] == "some/other-model"


def test_the_referer_header_is_sent_only_when_configured() -> None:
    connection = FakeConnection(status=200, body=success_body())

    client(connection).generate(request())
    _, _, _, headers_without = connection.calls[0]

    connection2 = FakeConnection(status=200, body=success_body())
    client(connection2, referer="https://career-agent.local").generate(request())
    _, _, _, headers_with = connection2.calls[0]

    assert "HTTP-Referer" not in headers_without
    assert headers_with["HTTP-Referer"] == "https://career-agent.local"


def test_bounded_generation_parameters_reach_the_request_body() -> None:
    connection = FakeConnection(status=200, body=success_body())

    client(connection).generate(request(max_output_tokens=123, temperature=0.5))

    sent = json.loads(connection.calls[0][2])
    assert (sent["max_tokens"], sent["temperature"]) == (123, 0.5)


# --- HTTP errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, LLMErrorCode.OTHER),
        (401, LLMErrorCode.UNAUTHORIZED),
        (403, LLMErrorCode.UNAUTHORIZED),
        (404, LLMErrorCode.OTHER),
        (429, LLMErrorCode.RATE_LIMITED),
        (500, LLMErrorCode.UNAVAILABLE),
        (503, LLMErrorCode.UNAVAILABLE),
    ],
)
def test_each_http_status_maps_to_a_stable_non_sensitive_code(
    status: int, code: LLMErrorCode
) -> None:
    connection = FakeConnection(status=status, body=b'{"error": "a message the server sent"}')

    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    assert excinfo.value.code is code
    assert "a message the server sent" not in str(excinfo.value)


# --- Transport failures ------------------------------------------------------------------------


def test_a_socket_timeout_is_reported_as_timeout() -> None:
    connection = FakeConnection(raises=TimeoutError())

    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    assert excinfo.value.code is LLMErrorCode.TIMEOUT
    assert connection.closed is True  # the connection is always closed, even on failure


def test_a_network_error_is_reported_as_unavailable() -> None:
    connection = FakeConnection(raises=ConnectionResetError("reset"))

    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    assert excinfo.value.code is LLMErrorCode.UNAVAILABLE


# --- Malformed or unusable responses ------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"[]",  # valid JSON, but not an object
        b'{"unexpected": "structure"}',  # no `choices` at all
        b'{"choices": []}',  # no choice to read
        b'{"choices": [{"message": {}}]}',  # a choice with no content
        b'{"choices": [{"message": {"content": ""}}]}',  # empty content: nothing exploitable
        b'{"choices": [{"message": {"content": "   "}}]}',  # blank content
        b'{"choices": [{"message": {"content": 42}}]}',  # content is not text
    ],
)
def test_an_unusable_response_is_invalid_response_never_a_fabricated_draft(body: bytes) -> None:
    connection = FakeConnection(status=200, body=body)

    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    assert excinfo.value.code is LLMErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'{"error": {"message": "rate limited", "code": 429}}', LLMErrorCode.RATE_LIMITED),
        (b'{"error": {"message": "no key", "code": 401}}', LLMErrorCode.UNAUTHORIZED),
        (b'{"error": {"message": "down", "code": 503}}', LLMErrorCode.UNAVAILABLE),
        (b'{"error": {"message": "something else"}}', LLMErrorCode.OTHER),
        (b'{"error": "a bare string, not an object"}', LLMErrorCode.OTHER),
    ],
)
def test_a_200_response_with_an_error_envelope_is_never_treated_as_success(
    body: bytes, code: LLMErrorCode
) -> None:
    """OpenRouter's free router can answer HTTP 200 with an error body when every free upstream
    failed. Treating that as a draft would fabricate content from nothing: it must raise."""
    connection = FakeConnection(status=200, body=body)

    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    assert excinfo.value.code is code


# --- Configuration -----------------------------------------------------------------------------


def test_a_missing_key_fails_closed_before_any_request_is_attempted() -> None:
    connection = FakeConnection(status=200, body=success_body())
    empty = OpenRouterClient(InMemorySecretStore(), "openrouter/free", connect=lambda: connection)

    with pytest.raises(LLMError) as excinfo:
        empty.generate(request())

    assert excinfo.value.code is LLMErrorCode.UNAUTHORIZED
    assert connection.calls == []  # no network attempt without a key


def test_a_present_key_is_read_through_the_secret_store_and_sent_as_a_bearer_token() -> None:
    connection = FakeConnection(status=200, body=success_body())

    client(connection).generate(request())

    _, _, _, headers = connection.calls[0]
    assert headers["Authorization"] == f"Bearer {SECRET_VALUE}"


def test_the_timeout_is_configurable_and_reaches_the_real_connection_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingConnection:
        def __init__(self, host: str, timeout: float) -> None:
            captured["host"] = host
            captured["timeout"] = timeout

    monkeypatch.setattr("app.integrations.llm.openrouter.HTTPSConnection", RecordingConnection)
    default_client = OpenRouterClient(secrets_with_key(), "openrouter/free")
    custom_client = OpenRouterClient(secrets_with_key(), "openrouter/free", timeout=5.0)

    default_client._connect()
    assert captured == {"host": "openrouter.ai", "timeout": DEFAULT_TIMEOUT_SECONDS}
    custom_client._connect()
    assert captured["timeout"] == 5.0


# --- Security ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "connection",
    [
        FakeConnection(status=401, body=b"{}"),
        FakeConnection(raises=TimeoutError()),
        FakeConnection(status=200, body=b"garbage"),
    ],
)
def test_no_exception_ever_carries_the_key_or_the_raw_response(
    connection: FakeConnection,
) -> None:
    with pytest.raises(LLMError) as excinfo:
        client(connection).generate(request())

    text = str(excinfo.value)
    assert SECRET_VALUE not in text
    assert text in {code.value for code in LLMErrorCode}  # only ever a bare code


def test_the_key_is_read_at_call_time_never_stored_on_the_client() -> None:
    connection = FakeConnection(status=200, body=success_body())
    instance = client(connection)

    assert not any(
        isinstance(value, str) and SECRET_VALUE in value for value in vars(instance).values()
    )
