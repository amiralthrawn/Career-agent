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
    """A call could not be completed. Always carries a CODE; MAY carry a short, sanitized
    `detail` for diagnosis - never a token, a header, MIME/attachment content, or the raw
    response body.

    `detail` is populated only for `401`/`403` (see
    `app.integrations.gmail.client._raise_for_status`), extracted from Gmail's OWN error
    envelope (`{"error": {"status": ..., "message": ...}}`) and nothing else - no other field of
    that envelope, and no field at all when the body isn't that exact shape.
    """

    def __init__(
        self, code: GmailErrorCode = GmailErrorCode.OTHER, *, detail: str | None = None
    ) -> None:
        super().__init__(code.value if detail is None else f"{code.value} ({detail})")
        self.code = code
        self.detail = detail


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
