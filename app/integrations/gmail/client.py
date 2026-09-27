"""GmailClient: the real adapter. Implements the EXISTING `MailProvider` protocol
(`app.integrations.mail.ports`) - `MailSender`/`SendGuard` need no change to use it.

`find_existing` is a best-effort idempotence check (Gmail's own `rfc822msgid:` search), used by
`app.services.send_batch` before RE-attempting a `failed` item: a network error can leave real
doubt about whether a message actually went out, and this lets a retry check rather than guess.
It is best-effort, not a cryptographic guarantee (see docs/send_batches.md's Limits section) - a
search index that has not caught up yet could still, rarely, miss a message just sent. The
database-level guarantee (at most one `sent` `SendBatchItem` per package, enforced by a unique
index) is the primary, always-correct protection against a genuine double SEND.

Verified against the real API: never in pytest, only through a manual, non-pytest smoke test
(`scripts/manual/gmail_smoke_test.py`) - see step 6/9's Perplexity/GitHub precedent.
"""

import base64
import json
from collections.abc import Callable
from http.client import HTTPSConnection
from urllib.parse import urlencode

from app.core.config import SendMode, Settings
from app.core.secrets import (
    GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    SecretStore,
    SecretStoreError,
)
from app.integrations.gmail.oauth import GmailOAuth
from app.integrations.gmail.ports import GmailError, GmailErrorCode, TokenSource
from app.integrations.http_transport import HTTPConnection
from app.integrations.mail.ports import BuiltMessage, SentMessage

API_HOST = "gmail.googleapis.com"
SEND_PATH = "/gmail/v1/users/me/messages/send"
SEARCH_PATH = "/gmail/v1/users/me/messages"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(API_HOST, timeout=timeout)


def _urlsafe_b64encode_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class GmailClient:
    def __init__(
        self,
        secrets: SecretStore,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Callable[[], HTTPConnection] | None = None,
        oauth: TokenSource | None = None,
    ) -> None:
        self._oauth = oauth or GmailOAuth(secrets, timeout=timeout)
        self._connect = connect or _default_connect(timeout)

    def send(self, message: BuiltMessage) -> SentMessage:
        token = self._oauth.access_token()
        payload = json.dumps({"raw": _urlsafe_b64encode_no_padding(message.raw)}).encode("utf-8")
        data = self._request("POST", SEND_PATH, token.value, body=payload)
        message_id = data.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise GmailError(GmailErrorCode.INVALID_RESPONSE)
        thread_id = data.get("threadId")
        return SentMessage(
            provider="gmail",
            provider_message_id=message_id,
            thread_id=thread_id if isinstance(thread_id, str) else None,
        )

    def find_existing(self, message_id: str) -> SentMessage | None:
        """Best-effort: `None` means "not found" - never treated as proof nothing was sent."""
        token = self._oauth.access_token()
        query = urlencode({"q": f"rfc822msgid:{message_id}"})
        data = self._request("GET", f"{SEARCH_PATH}?{query}", token.value)
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        if not isinstance(first, dict) or not isinstance(first.get("id"), str):
            return None
        thread_id = first.get("threadId")
        return SentMessage(
            provider="gmail",
            provider_message_id=first["id"],
            thread_id=thread_id if isinstance(thread_id, str) else None,
        )

    def _request(
        self, method: str, path: str, access_token: str, *, body: bytes = b""
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        connection = self._connect()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
        except TimeoutError:
            raise GmailError(GmailErrorCode.TIMEOUT) from None
        except OSError:
            raise GmailError(GmailErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()
        _raise_for_status(response.status)
        try:
            data = json.loads(content) if content else {}
        except (ValueError, TypeError):
            raise GmailError(GmailErrorCode.INVALID_RESPONSE) from None
        if not isinstance(data, dict):
            raise GmailError(GmailErrorCode.INVALID_RESPONSE)
        return data


def _raise_for_status(status: int) -> None:
    if status < 400:
        return
    if status in (401, 403):
        raise GmailError(GmailErrorCode.UNAUTHORIZED)
    if status == 429:
        raise GmailError(GmailErrorCode.RATE_LIMITED)
    if status >= 500:
        raise GmailError(GmailErrorCode.UNAVAILABLE)
    raise GmailError(GmailErrorCode.OTHER)


def default_gmail_provider(settings: Settings, secrets: SecretStore) -> GmailClient | None:
    """`None` by default: a real send is then refused, explicitly, before anything runs (the
    same "capabilities default off" convention as every other provider in this project) - only
    `SEND_MODE=auto` with every Gmail credential present returns a real client."""
    if settings.send_mode is not SendMode.AUTO:
        return None
    try:
        if not (
            secrets.exists(GMAIL_REFRESH_TOKEN)
            and secrets.exists(GMAIL_CLIENT_SECRET)
            and secrets.exists(GMAIL_CLIENT_ID)
        ):
            return None
    except SecretStoreError:
        return None  # fail closed: no secure backend, no client
    return GmailClient(secrets)
