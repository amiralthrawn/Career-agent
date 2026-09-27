"""ApplicationDraft: a proposed application text, never sent by itself (step 4).

The LLM is a generator, not a source of truth: `claims`, `selected_evidence` and `warnings` are
built by the APPLICATION from `PersonalizationBrief` (`RequirementMatch` + Candidate Brain
evidence states), never parsed out of the model's free text. Only `subject` and `body` come from
the model (or, for `subject`, are assembled by the app from already-validated facts); a human must
still read them before anything is used.

Content is immutable, like `TargetRequirement`: regenerating creates a NEW row and supersedes the
previous PROPOSED draft of the same kind (kept, not deleted). Only `status`, `decided_at` and
`decided_by` may change, through an explicit human decision (`approved` / `rejected`); a draft
replaced before any decision becomes `superseded` instead (`decided_at` stays null: no human
decided anything about it).
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
    Text,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import DraftKind, DraftStatus, enum_column
from app.models.immutability import make_immutable

# Columns that define what a draft MEANS. They can never be updated.
IMMUTABLE_DRAFT_COLUMNS = (
    "candidate_id",
    "target_id",
    "qualification_id",
    "kind",
    "model",
    "subject",
    "body",
    "claims",
    "selected_evidence",
    "warnings",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "duration_ms",
    "created_at",
)


class ApplicationDraft(CandidateOwnedMixin, Base):
    __tablename__ = "application_drafts"
    __table_args__ = (
        CheckConstraint("length(subject) > 0", name="subject_not_empty"),
        CheckConstraint("length(body) > 0", name="body_not_empty"),
        CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)",
            name="decided_at_iff_decided",
        ),
        # One PENDING (undecided) draft per (target, kind): a new generation supersedes it,
        # never leaves two proposals competing for the same human decision.
        Index(
            "uq_application_drafts_pending",
            "target_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'proposed'"),
            sqlite_where=text("status = 'proposed'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    # The qualification (and, through it, the requirement matches) this draft was built from.
    qualification_id: Mapped[int | None] = mapped_column(
        ForeignKey("qualifications.id", ondelete="SET NULL")
    )
    kind: Mapped[DraftKind] = mapped_column(enum_column(DraftKind))
    status: Mapped[DraftStatus] = mapped_column(
        enum_column(DraftStatus), default=DraftStatus.PROPOSED
    )
    # Identifier of the model that produced `body` (as the LLMClient reports it).
    model: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    # [{"requirement": "...", "facts": [{"type","id","name","state"}, ...]}]: app-derived, truthful.
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Deduplicated facts referenced by `claims`: exactly what was exposed to the model as evidence.
    selected_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # [{"code", "requirement", "text"}, ...]: gaps / weak / unmeasurable / open questions, from the
    # brief - never asserted as fact. Never derived from the model's own text.
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    usage_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The only mutable part: a human decision (or being superseded by a new generation).
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(16))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("application_drafts.id"))


make_immutable(ApplicationDraft.__table__, IMMUTABLE_DRAFT_COLUMNS)  # type: ignore[arg-type]


@event.listens_for(ApplicationDraft, "before_update")
def _refuse_content_change(mapper: Mapper[Any], connection: Any, target: ApplicationDraft) -> None:
    state = inspect(target)
    for column in IMMUTABLE_DRAFT_COLUMNS:
        if state.attrs[column].history.has_changes():
            raise RuntimeError("draft content is immutable: generate a new draft instead")
