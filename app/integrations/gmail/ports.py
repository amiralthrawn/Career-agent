"""Gmail-specific errors and the in-memory access token (step 10).

Sending itself reuses the EXISTING `app.integrations.mail.ports.MailProvider` protocol
unchanged: `GmailClient` implements `send(BuiltMessage) -> SentMessage` directly, so no new
send-port was needed, and every existing guarantee (`SendGuard`, `MailSender`'s guard-audit-
provider order, the RFC 5322 message built by `app.integrations.mail.mime`) applies to Gmail
exactly as it already applies to the dry-run provider.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class GmailErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"  # missing/invalid credentials, or a revoked refresh token
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"


class GmailError(Exception):
    """A call could not be completed. Carries only a CODE, never a message, a token or a payload."""

    def __init__(self, code: GmailErrorCode = GmailErrorCode.OTHER) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class AccessToken:
    """Kept in memory only, for the duration of one call - never persisted, never logged, never
    returned by any API route. `expires_at` is a `time.time()`-based epoch, already skewed a
    little early so a request never races the exact cutoff."""

    value: str
    expires_at: float


class TokenSource(Protocol):
    """What `GmailClient` needs from token management - `GmailOAuth` implements this, and a
    test fake may satisfy it directly without depending on the real HTTP-based class."""

    def access_token(self) -> AccessToken: ...
