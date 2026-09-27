"""GmailOAuth: HTTP transport is a fake (no socket, no real network, no real credentials)."""

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.core.secrets import (
    GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    InMemorySecretStore,
)
from app.integrations.gmail.oauth import GmailOAuth, authorize_url
from app.integrations.gmail.ports import GmailError, GmailErrorCode

CLIENT_ID = "client-id-fixture"  # noqa: S105 - synthetic
CLIENT_SECRET = "client-secret-fixture"  # noqa: S105 - synthetic
REFRESH_TOKEN = "refresh-token-fixture"  # noqa: S105 - synthetic


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


def secrets(*, with_refresh: bool = True) -> InMemorySecretStore:
    store = InMemorySecretStore()
    store.set(GMAIL_CLIENT_ID, CLIENT_ID)
    store.set(GMAIL_CLIENT_SECRET, CLIENT_SECRET)
    if with_refresh:
        store.set(GMAIL_REFRESH_TOKEN, REFRESH_TOKEN)
    return store


def token_response(
    access_token: str = "access-fixture",  # noqa: S107 - synthetic
    expires_in: int = 3600,
    refresh_token: str | None = None,
) -> bytes:
    data: dict[str, object] = {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    if refresh_token:
        data["refresh_token"] = refresh_token
    return json.dumps(data).encode()


# --- authorize_url ---------------------------------------------------------------------------


def test_authorize_url_carries_client_id_and_the_send_only_scope() -> None:
    url = authorize_url(CLIENT_ID, "http://localhost:8080/")

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert f"client_id={CLIENT_ID}" in url
    assert "gmail.send" in url
    assert "gmail.readonly" not in url and "gmail.modify" not in url


# --- access_token (refresh) --------------------------------------------------------------------


def test_access_token_refresh_success() -> None:
    connection = FakeConnection(body=token_response())

    token = GmailOAuth(secrets(), connect=lambda: connection).access_token()

    assert token.value == "access-fixture"  # noqa: S105 - synthetic
    (call,) = connection.calls
    method, path, body, headers = call
    assert method == "POST" and path == "/token"
    assert f"refresh_token={REFRESH_TOKEN}".encode() in body
    assert f"client_secret={CLIENT_SECRET}".encode() in body
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert connection.closed is True


def test_expiry_is_skewed_a_little_before_the_real_cutoff() -> None:
    before = time.time()
    connection = FakeConnection(body=token_response(expires_in=3600))

    token = GmailOAuth(secrets(), connect=lambda: connection).access_token()

    assert before + 3600 - 120 < token.expires_at < before + 3600


def test_no_refresh_token_stored_is_unauthorized() -> None:
    connection = FakeConnection(body=token_response())

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(with_refresh=False), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.UNAUTHORIZED
    assert connection.calls == []  # never even attempted without a refresh token


@pytest.mark.parametrize("status", [400, 401])
def test_a_rejected_refresh_is_unauthorized(status: int) -> None:
    connection = FakeConnection(status=status, body=b'{"error": "invalid_grant"}')

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.UNAUTHORIZED


def test_a_server_error_is_unavailable() -> None:
    connection = FakeConnection(status=503, body=b"{}")

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.UNAVAILABLE


def test_a_timeout_is_reported_as_timeout() -> None:
    connection = FakeConnection(raises=TimeoutError())

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.TIMEOUT


def test_a_connection_error_is_unavailable() -> None:
    connection = FakeConnection(raises=OSError())

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.UNAVAILABLE


def test_malformed_json_is_invalid_response() -> None:
    connection = FakeConnection(body=b"not json")

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.INVALID_RESPONSE


def test_a_response_missing_access_token_is_invalid_response() -> None:
    connection = FakeConnection(body=json.dumps({"expires_in": 3600}).encode())

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(), connect=lambda: connection).access_token()
    assert excinfo.value.code is GmailErrorCode.INVALID_RESPONSE


# --- exchange_code (one-time, manual setup only) ------------------------------------------------


def test_exchange_code_returns_the_refresh_token() -> None:
    connection = FakeConnection(body=token_response(refresh_token="brand-new-refresh"))

    refresh_token = GmailOAuth(
        secrets(with_refresh=False), connect=lambda: connection
    ).exchange_code("auth-code-fixture", "http://localhost:8080/")

    assert refresh_token == "brand-new-refresh"
    (call,) = connection.calls
    _, _, body, _ = call
    assert b"grant_type=authorization_code" in body
    assert b"code=auth-code-fixture" in body


def test_exchange_code_without_a_refresh_token_in_the_response_is_invalid() -> None:
    connection = FakeConnection(body=token_response())  # no refresh_token field

    with pytest.raises(GmailError) as excinfo:
        GmailOAuth(secrets(with_refresh=False), connect=lambda: connection).exchange_code(
            "auth-code-fixture", "http://localhost:8080/"
        )
    assert excinfo.value.code is GmailErrorCode.INVALID_RESPONSE


# --- no secret ever logged ----------------------------------------------------------------------


def test_no_print_or_logging_statement_exists_in_the_oauth_module() -> None:
    from pathlib import Path

    source = Path("app/integrations/gmail/oauth.py").read_text(encoding="utf-8")
    assert "print(" not in source and "logging." not in source and "logger." not in source
