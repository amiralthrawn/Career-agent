"""Builds the RFC 5322 message, attachments included, from an `OutgoingEmail`.

Pure and local: no network. Attachments are read (read-only) from the private data directory
and embedded byte for byte, so the recipient receives the original file untouched.
"""

import hashlib
import mimetypes
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from app.core.config import Settings
from app.core.errors import UnprocessableError
from app.integrations.mail.ports import (
    AttachmentRef,
    BuiltMessage,
    OutgoingEmail,
    recipient_domain,
)
from app.services.private_files import read_document_bytes, resolve_private_file

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ATTACHMENT_SUFFIXES = frozenset({".docx", ".pdf"})
_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


def _load_attachment(settings: Settings, ref: AttachmentRef) -> tuple[str, str, bytes]:
    path = resolve_private_file(settings, ref.relative_path, ATTACHMENT_SUFFIXES)
    data = read_document_bytes(path, max_bytes=MAX_ATTACHMENT_BYTES)
    name = ref.filename if ref.filename is not None else path.name
    if not name.strip() or any(char in name for char in '\r\n/\\"'):
        raise UnprocessableError("Invalid attachment file name")
    content_type = _CONTENT_TYPES.get(path.suffix.lower()) or (
        mimetypes.guess_type(name)[0] or "application/octet-stream"
    )
    return name, content_type, data


def build_message(
    settings: Settings, email: OutgoingEmail, *, now: datetime | None = None
) -> BuiltMessage:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = email.sender
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Date"] = format_datetime(now or datetime.now(UTC))
    # A caller-supplied id (step 10) is reused verbatim, so a retry of the SAME logical send can
    # be looked up by it; otherwise a fresh, random one (unchanged pre-step-10 behaviour).
    message_id = email.message_id or make_msgid(domain=recipient_domain(email.sender))
    message["Message-ID"] = message_id
    message.set_content(email.body_text, charset="utf-8")

    digests: list[str] = []
    total = 0
    for ref in email.attachments:
        name, content_type, data = _load_attachment(settings, ref)
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        digests.append(hashlib.sha256(data).hexdigest())
        total += len(data)

    return BuiltMessage(
        raw=message.as_bytes(),
        sender=email.sender,
        to=email.to,
        message_id=message_id,
        attachment_count=len(digests),
        attachment_bytes=total,
        attachment_sha256=tuple(digests),
    )
