"""Campaign: chains as many sourcing searches as needed toward a daily target, in ONE bounded,
human-started run - see `app.services.campaign`.

This is the one deliberate, explicitly-authorized exception to this project's own "no autonomous
agent" rule (a human decides to accept that trade-off for THIS feature only, after being shown
the conflict): a campaign still runs to completion inside a single process a human explicitly
starts (the CLI's own foreground process) and stops itself, on its own, at the FIRST of several
safety limits - it is never a background daemon, a scheduler, or something that re-triggers
itself. Nothing here bypasses `SendGuard` or the human validation of a draft/package: a campaign
can prepare packages, never approve or send one.

Not a second sourcing engine: every round is one ordinary `SourcingService.run()` call, reusing
its own dedup/provenance/qualification exactly as a manual search would. `SearchRun.campaign_id`
links each round back to the campaign that started it (`NULL` for a manually-triggered run).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import CampaignStatus, SourcingMode, enum_column


class Campaign(CandidateOwnedMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("search_profiles.id", ondelete="CASCADE"))
    mode: Mapped[SourcingMode] = mapped_column(enum_column(SourcingMode))
    provider: Mapped[str] = mapped_column(String(64))
    max_results_per_call: Mapped[int] = mapped_column(Integer)

    # --- the objective and its safety limits (all fixed at creation) -------------------------
    daily_target: Mapped[int] = mapped_column(Integer)
    max_calls: Mapped[int] = mapped_column(Integer)
    max_duration_minutes: Mapped[int] = mapped_column(Integer)
    max_consecutive_empty: Mapped[int] = mapped_column(Integer)

    status: Mapped[CampaignStatus] = mapped_column(
        enum_column(CampaignStatus), default=CampaignStatus.RUNNING
    )
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- running counters, updated after each round so a concurrent read sees live progress ---
    calls_made: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consecutive_empty_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    targets_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requirements_extracted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    contacts_proposed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    drafts_generated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    packages_prepared: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
