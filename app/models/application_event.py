"""Application lifecycle tracking (step 11): a chronological, immutable, append-only history of
real-world facts about one candidature's journey - never a score, never a status Career-agent
invents on its own authority.

**Never considered received/rejected/accepted without sufficient evidence.** `status` reuses the
EXISTING `InfoStatus` (FOUND/UNCERTAIN, `app.models.enums`): `FOUND` means confirmed (a human's
own explicit report, or Career-agent's own certain action - prepared/approved/sent), `UNCERTAIN`
means detected but not independently confirmed (a future Gmail classifier's guess). Absence of a
row is absence of information, never a negative claim - the same "absence ≠ negation" rule applied
everywhere else in this project. Nothing here ever auto-promotes an `UNCERTAIN` event to `FOUND`;
only an explicit human correction does (see `app.services.application_tracking`).

**A correction is a NEW row, never an edit.** Every column is immutable (database trigger, same
convention as `CompanyResearchFact`/`ApplicationDraft`'s content columns): `corrected_event_id`
points FORWARD from a correction to the event it corrects, set once at creation, never mutated -
the original row is never deleted or silently overwritten, so the full history stays
reconstructible exactly as it was recorded.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import ApplicationEventOrigin, ApplicationEventType, InfoStatus, enum_column
from app.models.immutability import make_immutable

MAX_NOTE_CHARS = 2000  # a short human note, never a full e-mail body (never stored here)


class ApplicationEvent(CandidateOwnedMixin, Base):
    __tablename__ = "application_events"
    __table_args__ = (
        # Dedup for machine-sourced events with a real, checkable reference (a Gmail message id):
        # the SAME reference can never be recorded twice as a FRESH, independent finding for the
        # SAME package. A pure manual note (no reference) is never deduplicated this way - a
        # human may legitimately log two distinct notes with nothing machine-checkable to
        # compare. A CORRECTION is deliberately exempt (`corrected_event_id IS NULL` below): it
        # is expected, by design, to reuse the exact same reference as the row it corrects (e.g.
        # confirming an uncertain event only changes `status`, keeping the same message id).
        Index(
            "uq_application_events_reference",
            "application_package_id",
            "event_type",
            "reference",
            unique=True,
            postgresql_where=text("reference IS NOT NULL AND corrected_event_id IS NULL"),
            sqlite_where=text("reference IS NOT NULL AND corrected_event_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_package_id: Mapped[int] = mapped_column(
        ForeignKey("application_packages.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[ApplicationEventType] = mapped_column(
        enum_column(ApplicationEventType), index=True
    )
    origin: Mapped[ApplicationEventOrigin] = mapped_column(enum_column(ApplicationEventOrigin))
    status: Mapped[InfoStatus] = mapped_column(enum_column(InfoStatus))
    # When the real-world fact happened (human-supplied or detected) - distinct from `created_at`
    # (TimestampMixin would call this "recorded_at": when CAREER-AGENT learned of it).
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # An external, checkable id when one exists (a Gmail message id, a thread id, a provider
    # message id already recorded on the SendBatchItem that produced a `sent` event). Never a
    # free-form description - see `note` for that, bounded and never full message content.
    reference: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    # What this row corrects, if it is a correction - never mutated after creation, and the
    # corrected row itself is never touched: both stay in the history exactly as recorded.
    corrected_event_id: Mapped[int | None] = mapped_column(ForeignKey("application_events.id"))
    # Free-form, non-sensitive extra fields specific to one event_type (e.g. an interview's
    # format) - never recipient content, never a secret; same discipline as SendBatchItem.details.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


make_immutable(ApplicationEvent.__table__)  # type: ignore[arg-type]
