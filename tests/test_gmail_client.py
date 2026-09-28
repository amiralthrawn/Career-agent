"""GmailClient: HTTP transport AND OAuth are fakes (no socket, no real network, no real token)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from app.core.config import Settings
from app.core.secrets import (
    GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    InMemorySecretStore,
)
from app.integrations.gmail.client import GmailClient, default_gmail_provider
from app.integrations.gmail.ports import AccessToken, GmailError, GmailErrorCode
from app.integrations.mail.ports import BuiltMessage


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


class FakeOAuth:
    """A fake `GmailOAuth`: hands back a fixed token, no HTTP at all."""

    def __init__(self, token: str = "access-fixture") -> None:  # noqa: S107 - synthetic
        self.token = token
        self.calls = 0

    def access_token(self) -> AccessToken:
        self.calls += 1
        return AccessToken(value=self.token, expires_at=9999999999.0)


def built_message(
    raw: bytes = b"From: a@b.invalid\r\nTo: c@d.invalid\r\n\r\nhello",
) -> BuiltMessage:
    return BuiltMessage(
        raw=raw,
        sender="a@b.invalid",
        to="c@d.invalid",
        message_id="<fixture@b.invalid>",
        attachment_count=0,
        attachment_bytes=0,
    )


def client(connection: FakeConnection, oauth: FakeOAuth | None = None) -> GmailClient:
    return GmailClient(
        InMemorySecretStore(), connect=lambda: connection, oauth=oauth or FakeOAuth()
    )


# --- send ------------------------------------------------------------------------------------


def test_send_posts_the_raw_message_base64url_encoded_and_returns_ids() -> None:
    connection = FakeConnection(
        body=json.dumps({"id": "msg-123", "threadId": "thread-456"}).encode()
    )

    sent = client(connection).send(built_message())

    assert sent.provider == "gmail"
    assert sent.provider_message_id == "msg-123"
    assert sent.thread_id == "thread-456"
    (call,) = connection.calls
    method, path, body, headers = call
    assert method == "POST" and path == "/gmail/v1/users/me/messages/send"
    assert headers["Authorization"] == "Bearer access-fixture"
    payload = json.loads(body)
    assert "+" not in payload["raw"] and "/" not in payload["raw"]  # URL-safe base64, no padding
    assert "=" not in payload["raw"]


def test_send_without_a_thread_id_still_works() -> None:
    connection = FakeConnection(body=json.dumps({"id": "msg-123"}).encode())

    sent = client(connection).send(built_message())

    assert sent.thread_id is None


def test_a_missing_message_id_in_the_response_is_invalid() -> None:
    connection = FakeConnection(body=b"{}")

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())
    assert excinfo.value.code is GmailErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, GmailErrorCode.UNAUTHORIZED),
        (403, GmailErrorCode.UNAUTHORIZED),
        (429, GmailErrorCode.RATE_LIMITED),
        (500, GmailErrorCode.UNAVAILABLE),
        (418, GmailErrorCode.OTHER),
    ],
)
def test_status_codes_map_to_stable_error_codes(status: int, code: GmailErrorCode) -> None:
    connection = FakeConnection(status=status, body=b"{}")

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())
    assert excinfo.value.code is code


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_expose_a_sanitized_detail_from_gmails_error_envelope(status: int) -> None:
    body = json.dumps(
        {
            "error": {
                "code": status,
                "message": "Request had insufficient authentication scopes.",
                "status": "PERMISSION_DENIED",
                "errors": [{"message": "insufficient scope", "domain": "global"}],
            }
        }
    ).encode()
    connection = FakeConnection(status=status, body=body)

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())

    assert excinfo.value.code is GmailErrorCode.UNAUTHORIZED
    detail = excinfo.value.detail
    assert detail is not None
    assert "PERMISSION_DENIED" in detail
    assert "insufficient authentication scopes" in detail
    # Only the two allowed fields ever make it through - not the nested "errors" list/domain.
    assert "domain" not in detail and "global" not in detail


def test_a_non_json_401_body_yields_no_detail_but_still_the_right_code() -> None:
    connection = FakeConnection(status=401, body=b"not json at all")

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())

    assert excinfo.value.code is GmailErrorCode.UNAUTHORIZED
    assert excinfo.value.detail is None


def test_a_401_body_without_the_expected_shape_yields_no_detail() -> None:
    connection = FakeConnection(status=401, body=b'{"unexpected": "shape"}')

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())

    assert excinfo.value.detail is None


def test_no_token_or_secret_ever_appears_in_the_error_message() -> None:
    """The response body legitimately never contains our own token, but this stays defensive:
    even if Gmail echoed something token-shaped, only `status`/`message` are ever read out."""
    real_looking_token = "ya29.a0AfH6SMC-this-looks-like-a-real-access-token-1234567890"  # noqa: S105
    body = json.dumps(
        {
            "error": {
                "message": "Invalid Credentials",
                "status": "UNAUTHENTICATED",
                "authorization_header": f"Bearer {real_looking_token}",
            }
        }
    ).encode()
    connection = FakeConnection(status=401, body=body)

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())

    assert real_looking_token not in str(excinfo.value)
    assert excinfo.value.detail is not None and real_looking_token not in excinfo.value.detail


def test_429_and_5xx_still_carry_no_detail_only_401_403_do() -> None:
    connection = FakeConnection(status=429, body=b'{"error": {"status": "X", "message": "Y"}}')

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())

    assert excinfo.value.code is GmailErrorCode.RATE_LIMITED
    assert excinfo.value.detail is None


def test_a_timeout_is_reported_as_timeout() -> None:
    connection = FakeConnection(raises=TimeoutError())

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())
    assert excinfo.value.code is GmailErrorCode.TIMEOUT


def test_a_malformed_json_response_is_invalid_response() -> None:
    connection = FakeConnection(body=b"not json at all")

    with pytest.raises(GmailError) as excinfo:
        client(connection).send(built_message())
    assert excinfo.value.code is GmailErrorCode.INVALID_RESPONSE


def test_the_connection_is_always_closed() -> None:
    connection = FakeConnection(body=json.dumps({"id": "msg-1"}).encode())

    client(connection).send(built_message())

    assert connection.closed is True


# --- find_existing (best-effort idempotence check) ---------------------------------------------


def test_find_existing_returns_the_first_match() -> None:
    connection = FakeConnection(
        body=json.dumps({"messages": [{"id": "found-1", "threadId": "t-1"}]}).encode()
    )

    found = client(connection).find_existing("<fixture@b.invalid>")

    assert found is not None and found.provider_message_id == "found-1"
    (call,) = connection.calls
    method, path, _, _ = call
    assert method == "GET" and "rfc822msgid" in path and "fixture%40b.invalid" in path


def test_find_existing_returns_none_when_nothing_matches() -> None:
    connection = FakeConnection(body=json.dumps({}).encode())

    assert client(connection).find_existing("<fixture@b.invalid>") is None


def test_find_existing_returns_none_on_an_empty_messages_list() -> None:
    connection = FakeConnection(body=json.dumps({"messages": []}).encode())

    assert client(connection).find_existing("<fixture@b.invalid>") is None


# --- default_gmail_provider (capability gate) ---------------------------------------------------


def _full_secrets() -> InMemorySecretStore:
    store = InMemorySecretStore()
    store.set(GMAIL_CLIENT_ID, "id-fixture")
    store.set(GMAIL_CLIENT_SECRET, "secret-fixture")  # noqa: S105 - synthetic
    store.set(GMAIL_REFRESH_TOKEN, "refresh-fixture")  # noqa: S105 - synthetic
    return store


def test_disabled_send_mode_gives_no_provider() -> None:
    settings = Settings(send_mode="dry_run")
    assert default_gmail_provider(settings, _full_secrets()) is None


def test_auto_mode_without_credentials_gives_no_provider() -> None:
    settings = Settings(send_mode="auto")
    assert default_gmail_provider(settings, InMemorySecretStore()) is None


def test_auto_mode_with_every_credential_gives_a_real_client() -> None:
    settings = Settings(send_mode="auto")

    provider = default_gmail_provider(settings, _full_secrets())

    assert provider is not None and type(provider).__name__ == "GmailClient"


# --- no secret ever logged ----------------------------------------------------------------------


def test_no_print_or_logging_statement_exists_in_the_client_module() -> None:
    from pathlib import Path

    source = Path("app/integrations/gmail/client.py").read_text(encoding="utf-8")
    assert "print(" not in source and "logging." not in source and "logger." not in source


def test_gmail_client_satisfies_the_idempotent_mail_provider_protocol() -> None:
    from app.integrations.mail.dry_run import DryRunMailProvider
    from app.integrations.mail.ports import IdempotentMailProvider

    gmail = client(FakeConnection())
    assert isinstance(gmail, IdempotentMailProvider)

    # The dry-run provider has no `find_existing`: it must NOT satisfy the protocol.
    dry_run = DryRunMailProvider.__new__(DryRunMailProvider)
    assert not isinstance(dry_run, IdempotentMailProvider)


def test_the_access_token_is_used_but_never_stored_by_the_client() -> None:
    """The client never persists what `access_token()` returns - it is only read fresh."""
    oauth = FakeOAuth()
    connection = FakeConnection(body=json.dumps({"id": "msg-1"}).encode())
    gmail = client(connection, oauth)

    gmail.send(built_message())
    gmail.send(built_message())

    assert oauth.calls == 2  # a fresh token is asked for every call, never cached by the client
