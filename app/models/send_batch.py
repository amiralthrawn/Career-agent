"""Controlled batch sending (step 10): a `SendBatch` groups already-individually-approved
`ApplicationPackage`s (step 9, unchanged) so a human can approve and send many at once, instead
of opening Gmail and clicking send N times - while keeping the SAME explicit human control and
the SAME per-item traceability as sending one at a time.

**Two layers of approval, one send mechanism.** Layer 1 is `ApplicationPackage.status ==
APPROVED` (a human read THIS candidature's actual content). Layer 2 is `SendBatch.status ==
APPROVED` (a human explicitly confirmed THIS group should be sent). `SendBatchItem` re-validates
every precondition again at execution time (a package can go stale, a contact can stop being
accepted, between validation and send) - neither approval is ever treated as a stale snapshot
good forever. A single package is sent through a batch of exactly one item: there is no separate,
parallel "individual send" code path (see `app.services.send_batch`).

Unlike `ApplicationDraft`/`ApplicationPackage`, nothing here is immutable: a `SendBatchItem` is
operational tracking state (attempts, outcome), not generated content - closer to
`ContactResearchObservation`'s mutable-in-place convention than to the append-only drafts/
packages.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import SendBatchItemStatus, SendBatchStatus, enum_column


class SendBatch(CandidateOwnedMixin, TimestampMixin, Base):
    __tablename__ = "send_batches"
    __table_args__ = (
        UniqueConstraint("candidate_id", "idempotency_key", name="uq_send_batches_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[SendBatchStatus] = mapped_column(
        enum_column(SendBatchStatus), default=SendBatchStatus.DRAFT, index=True
    )
    # A stable id carried on every audit event of this batch, so its full history is
    # reconstructible ("send_batch.created", "...approved", "send_batch.send_requested" per item,
    # "send.sent"/"send.failed" per item, "...completed"/"...partially_failed").
    correlation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # Optional, client-supplied: a repeated `create` with the same key returns the same batch
    # instead of creating a second one (batch-creation idempotence). NULL never collides
    # (partial behaviour of a plain UniqueConstraint on SQLite/PostgreSQL: two NULLs are distinct).
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(16))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(16))


class SendBatchItem(TimestampMixin, Base):
    __tablename__ = "send_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "application_package_id", name="uq_send_batch_items_package"),
        # The core idempotence guarantee, enforced at the database level regardless of
        # application bugs or races: at most one SENT row can ever exist for a given package,
        # across every batch it was ever part of.
        Index(
            "uq_send_batch_items_sent_once",
            "application_package_id",
            unique=True,
            postgresql_where=text("status = 'sent'"),
            sqlite_where=text("status = 'sent'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("send_batches.id", ondelete="CASCADE"), index=True
    )
    application_package_id: Mapped[int] = mapped_column(
        ForeignKey("application_packages.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[SendBatchItemStatus] = mapped_column(
        enum_column(SendBatchItemStatus), default=SendBatchItemStatus.PENDING, index=True
    )
    # Short machine-readable code (e.g. "stale", "do_not_contact", "no_email", "provider_error");
    # never a message with recipient/content in it.
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    thread_id: Mapped[str | None] = mapped_column(String(255))
    # The exact Message-ID this attempt (or its retries) used - lets a retry ask the provider
    # "was this already sent?" before trying again (see app.integrations.gmail.client).
    message_id: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-form, non-sensitive counters/details of the last attempt (never recipient or content).
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
