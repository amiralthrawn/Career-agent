"""Dry-run provider: writes the message as a local .eml file. No network, no real send."""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import ConflictError, UnprocessableError
from app.integrations.mail.ports import BuiltMessage, SentMessage

OUTBOX_DIRECTORY_NAME = "outbox"


class DryRunMailProvider:
    """Writes `.eml` files into `<private data>/outbox/` and nowhere else."""

    def __init__(self, private_root: Path, outbox: Path) -> None:
        self._root = private_root.resolve()
        self._outbox = outbox
        resolved = outbox.resolve()
        if resolved.parent != self._root or resolved.name != OUTBOX_DIRECTORY_NAME:
            raise UnprocessableError("The outbox must be data/private/outbox")

    def send(self, message: BuiltMessage) -> SentMessage:
        self._outbox.mkdir(parents=True, exist_ok=True)
        if self._outbox.resolve().parent != self._root:  # e.g. the folder became a link
            raise UnprocessableError("The outbox must be data/private/outbox")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        digest = hashlib.sha256(message.message_id.encode()).hexdigest()[:10]
        # The name carries no recipient, subject or person name.
        path = self._outbox / f"{stamp}-{digest}.eml"
        if path.exists():
            raise ConflictError("This message has already been written to the outbox")
        temporary = path.with_suffix(".eml.part")
        with temporary.open("xb") as handle:  # never overwrites an existing file
            handle.write(message.raw)
        os.replace(temporary, path)
        return SentMessage(
            provider="dry_run",
            provider_message_id=message.message_id,
            thread_id=None,
            location=f"{OUTBOX_DIRECTORY_NAME}/{path.name}",
        )
