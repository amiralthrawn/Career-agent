"""Gmail OAuth 2.0 (step 10): "installed application" flow, loopback/manual redirect, scope
`gmail.send` only (send, never read - see docs/mail-architecture.md, decided at step 1).

Never a Gmail password. Only the refresh token is stored, through `SecretStore`
(`gmail_refresh_token`); the client id/secret also live there (`gmail_client_id`,
`gmail_client_secret`) so every Gmail credential is in one place, never in code or `.env`. An
access token is exchanged on demand and kept in memory only for the duration of one call - never
persisted, never logged, never returned by any API route.

`authorize_url`/`exchange_code` are the ONE step that genuinely needs a human and a browser (a
consent screen click): they are used by the manual setup script
(`scripts/manual/gmail_oauth_setup.py`), never by the automated API or by pytest.
"""

import json
import time
from collections.abc import Callable
from http.client import HTTPSConnection
from urllib.parse import urlencode

from app.core.secrets import (
    GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    SecretStore,
    SecretStoreError,
)
from app.integrations.gmail.ports import AccessToken, GmailError, GmailErrorCode
from app.integrations.http_transport import HTTPConnection

TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_PATH = "/token"
AUTH_HOST = "accounts.google.com"
AUTH_PATH = "/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
DEFAULT_TIMEOUT_SECONDS = 20.0
# Refresh a little before actual expiry, so a request never races the exact cutoff.
EXPIRY_SKEW_SECONDS = 60


def authorize_url(client_id: str, redirect_uri: str) -> str:
    """The URL a human opens once, manually, to grant `gmail.send`."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://{AUTH_HOST}{AUTH_PATH}?{urlencode(params)}"


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(TOKEN_HOST, timeout=timeout)


class GmailOAuth:
    def __init__(
        self,
        secrets: SecretStore,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Callable[[], HTTPConnection] | None = None,
    ) -> None:
        self._secrets = secrets
        self._connect = connect or _default_connect(timeout)

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        """One-time: turns a fresh authorization code into a refresh token, returned so the
        manual setup script can store it - never logged by this method."""
        payload = self._call(
            {
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GmailError(GmailErrorCode.INVALID_RESPONSE)
        return refresh_token

    def access_token(self) -> AccessToken:
        """A fresh access token, exchanged from the stored refresh token. Never persisted."""
        payload = self._call(
            {
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "refresh_token": self._refresh_token(),
                "grant_type": "refresh_token",
            }
        )
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(expires_in, int | float):
            raise GmailError(GmailErrorCode.INVALID_RESPONSE)
        return AccessToken(value=token, expires_at=time.time() + expires_in - EXPIRY_SKEW_SECONDS)

    def _client_id(self) -> str:
        return self._secret(GMAIL_CLIENT_ID)

    def _client_secret(self) -> str:
        return self._secret(GMAIL_CLIENT_SECRET)

    def _refresh_token(self) -> str:
        return self._secret(GMAIL_REFRESH_TOKEN)

    def _secret(self, name: str) -> str:
        try:
            value = self._secrets.get(name)
        except SecretStoreError:
            raise GmailError(GmailErrorCode.UNAUTHORIZED) from None
        if not value:
            raise GmailError(GmailErrorCode.UNAUTHORIZED)
        return value

    def _call(self, fields: dict[str, str]) -> dict[str, object]:
        body = urlencode(fields).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        connection = self._connect()
        try:
            connection.request("POST", TOKEN_PATH, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
        except TimeoutError:
            raise GmailError(GmailErrorCode.TIMEOUT) from None
        except OSError:
            raise GmailError(GmailErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()
        if response.status >= 400:
            code = (
                GmailErrorCode.UNAUTHORIZED
                if response.status in (400, 401)
                else GmailErrorCode.UNAVAILABLE
            )
            raise GmailError(code)
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            raise GmailError(GmailErrorCode.INVALID_RESPONSE) from None
        if not isinstance(data, dict):
            raise GmailError(GmailErrorCode.INVALID_RESPONSE)
        return data
