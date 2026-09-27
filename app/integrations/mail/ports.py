"""Mail ports: what the application needs from any mail provider (Gmail later).

The application builds a complete, validated RFC 5322 message (`BuiltMessage`) and hands it to
a provider. A provider only transports it; it never decides whether it may be sent (that is
the `SendGuard`'s job) and never sees anything else of the application.
"""

import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Protocol, runtime_checkable

from app.core.errors import UnprocessableError

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 50_000
MAX_ATTACHMENTS = 5
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def validate_address(value: str) -> str:
    """One plain address: no display name, no list, no line break (header injection)."""
    address = value.strip()
    if any(char in address for char in "\r\n,;<> "):
        raise UnprocessableError("Invalid e-mail address")
    if parseaddr(address)[1] != address or not _ADDRESS_RE.match(address):
        raise UnprocessableError("Invalid e-mail address")
    return address


def recipient_domain(address: str) -> str:
    return validate_address(address).rsplit("@", 1)[1].lower()


@dataclass(frozen=True)
class AttachmentRef:
    """A file of the private data directory, given relative to it (e.g. `documents/cv.docx`)."""

    relative_path: str
    filename: str | None = None  # name shown to the recipient; defaults to the file name


@dataclass(frozen=True)
class OutgoingEmail:
    sender: str
    to: str
    subject: str
    body_text: str
    attachments: tuple[AttachmentRef, ...] = ()
    # Optional (step 10): a caller-supplied `Message-ID`, reused verbatim across retries of the
    # SAME logical send so a provider that supports `IdempotentMailProvider.find_existing` can be
    # asked "was this already sent?" before trying again. `None` (every pre-step-10 caller) keeps
    # the original behaviour: `build_message` generates a fresh, random one.
    message_id: str | None = None

    def __post_init__(self) -> None:
        validate_address(self.sender)
        validate_address(self.to)
        if not self.subject.strip() or len(self.subject) > MAX_SUBJECT_CHARS:
            raise UnprocessableError("The subject is empty or too long")
        if "\r" in self.subject or "\n" in self.subject:
            raise UnprocessableError("The subject must be a single line")
        if not self.body_text.strip() or len(self.body_text) > MAX_BODY_CHARS:
            raise UnprocessableError("The body is empty or too long")
        if len(self.attachments) > MAX_ATTACHMENTS:
            raise UnprocessableError(f"At most {MAX_ATTACHMENTS} attachments are allowed")
        if self.message_id is not None:
            value = self.message_id.strip()
            if not value or "\r" in value or "\n" in value:
                raise UnprocessableError("message_id must be a non-empty, single-line value")


@dataclass(frozen=True)
class BuiltMessage:
    """A ready-to-transport message. `raw` is the complete RFC 5322 message."""

    raw: bytes
    sender: str
    to: str
    message_id: str
    attachment_count: int
    attachment_bytes: int
    attachment_sha256: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class SentMessage:
    """What a provider reports back; `provider_message_id` is what tracking will rely on."""

    provider: str
    provider_message_id: str
    thread_id: str | None = None
    location: str | None = None  # for dry-run: the .eml path relative to the private directory


class MailProvider(Protocol):
    """Transport of an already validated message."""

    def send(self, message: BuiltMessage) -> SentMessage: ...


@runtime_checkable
class IdempotentMailProvider(MailProvider, Protocol):
    """A `MailProvider` that can also answer "was this already sent?" (step 10).

    Best-effort, not a guarantee (see docs/send_batches.md): used by
    `app.services.send_batch.SendBatchService` before RE-attempting a `failed` item, so a network
    error that left real doubt about whether a message went out is checked rather than guessed.
    `GmailClient` implements this; the dry-run provider (and any other `MailProvider` that does
    not) simply does not satisfy it, and callers fall back to a plain retry.
    """

    def find_existing(self, message_id: str) -> SentMessage | None: ...
