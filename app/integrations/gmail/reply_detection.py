"""Reply detection (step 11, preparation only): the port a future Gmail-reading integration
would implement, so `app.services.application_tracking` can consume detected replies without
depending on any real Gmail read access.

**No real implementation exists.** The current OAuth grant (step 10) is `gmail.send` only - it
cannot read a mailbox at all. Reading replies for real needs a WIDER scope (`gmail.readonly` at
minimum), which this project does not request and will not request silently: see
docs/application_tracking.md's "Connecting Gmail for real reply detection" section for exactly
what scope is needed and why a human must explicitly approve that widened grant first.

Every `ReplyEvidence` this port could ever produce is consumed as `UNCERTAIN`
(`app.services.application_tracking.record_detected_events`) - never auto-confirmed, whatever a
classifier's `suggested_type` guesses.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models.enums import ApplicationEventType


@dataclass(frozen=True)
class ReplyEvidence:
    """One candidate signal a (future) Gmail reader would report - a GUESS, never a decision."""

    message_id: str
    thread_id: str | None
    received_at: datetime
    # Domain only, never a full address - the same discipline `SendGuard`/audit already apply.
    from_domain: str | None = None
    # A classifier's guess at what this might be; `None` if it has no specific idea at all.
    suggested_type: ApplicationEventType | None = None
    excerpt: str | None = None  # short, bounded; never a full message body


class ReplyDetectionProvider(Protocol):
    def detect_replies(
        self, thread_ids: Sequence[str], *, since: datetime
    ) -> Sequence[ReplyEvidence]: ...


class SimulatedReplyDetectionProvider:
    """A fake, for tests only: returns exactly the scripted evidence it was given. No network,
    no real Gmail - stands in for a not-yet-built real implementation (see module docstring)."""

    def __init__(self, evidence: Sequence[ReplyEvidence] = ()) -> None:
        self._evidence = tuple(evidence)
        self.calls: list[tuple[tuple[str, ...], datetime]] = []

    def detect_replies(
        self, thread_ids: Sequence[str], *, since: datetime
    ) -> Sequence[ReplyEvidence]:
        self.calls.append((tuple(thread_ids), since))
        wanted = set(thread_ids)
        return tuple(e for e in self._evidence if e.thread_id in wanted and e.received_at >= since)
