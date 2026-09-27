"""Sourcing runs (step 3c): an auditable record of one search, not an event bus.

A `SearchRun` says who searched what (profile, mode, provider, structured query), how it ended and
with which counters. A `SearchRunItem` says, for each result the run looked at, what became of it
(target created / existing, rejected with a reason code, error) with a reference to the resulting
target and qualification.

Deliberately NOT stored: provider payloads, headers, credentials, the raw response. Only the
public URL of a rejected result and a short excerpt are kept, for review.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import (
    ItemReason,
    ProviderKind,
    SearchRunItemOutcome,
    SearchRunStatus,
    SourcingMode,
    enum_column,
)

COUNTER_COLUMNS = (
    "results_raw",
    "targets_created",
    "targets_existing",
    "companies_created",
    "opportunities_created",
    "qualifications_created",
    "items_rejected",
    "item_errors",
)


class SearchRun(CandidateOwnedMixin, Base):
    __tablename__ = "search_runs"
    __table_args__ = (
        Index("ix_search_runs_profile_started", "profile_id", "started_at"),
        Index("ix_search_runs_status", "status"),
        # A run is `running` exactly until it has an end time.
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="running_iff_unfinished"
        ),
        CheckConstraint("max_results > 0", name="max_results_positive"),
        CheckConstraint(
            " AND ".join(f"{column} >= 0" for column in COUNTER_COLUMNS),
            name="counters_not_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("search_profiles.id", ondelete="CASCADE"))
    mode: Mapped[SourcingMode] = mapped_column(enum_column(SourcingMode))
    provider: Mapped[str] = mapped_column(String(64))
    provider_kind: Mapped[ProviderKind] = mapped_column(enum_column(ProviderKind))
    status: Mapped[SearchRunStatus] = mapped_column(
        enum_column(SearchRunStatus), default=SearchRunStatus.RUNNING
    )
    # The structured query: search terms of the profile by dimension. No candidate data.
    query: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_results: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results_raw: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    targets_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    targets_existing: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    companies_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    opportunities_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    qualifications_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_rejected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    item_errors: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # [{"code": "provider_error", "detail": "rate_limited"}]: codes only, never a message.
    errors: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    # Labels of the sources the provider says it consulted (short, provider-declared).
    sources_consulted: Mapped[list[str]] = mapped_column(JSON, default=list)


class SearchRunItem(Base):
    __tablename__ = "search_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "position"),
        # A rejected or failed item says why; a successful one references its target.
        CheckConstraint(
            "(outcome IN ('rejected', 'error')) = (reason IS NOT NULL)", name="reason_iff_failed"
        ),
        # (a target may be deleted later: the reference then becomes NULL, hence one direction only)
        CheckConstraint(
            "outcome NOT IN ('rejected', 'error') OR target_id IS NULL",
            name="no_target_when_failed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[SearchRunItemOutcome] = mapped_column(enum_column(SearchRunItemOutcome))
    reason: Mapped[ItemReason | None] = mapped_column(enum_column(ItemReason))
    # Names of the invalid fields ("company.website_url"), never their values.
    fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("targets.id", ondelete="SET NULL"))
    qualification_id: Mapped[int | None] = mapped_column(
        ForeignKey("qualifications.id", ondelete="SET NULL")
    )
    # Public URL of the result, kept only when the item did not become a target (for review).
    source_url: Mapped[str | None] = mapped_column(String(2048))
    # The relevant source text as read, truncated. Not the provider response.
    excerpt: Mapped[str | None] = mapped_column(String(500))
