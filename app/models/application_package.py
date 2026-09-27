"""ApplicationPackage: a Target turned into an exploitable, reviewable candidature (step 9).

**This is not a second draft system.** `ApplicationDraft` (step 4) already holds the generated
text, its `claims`/`selected_evidence`/`warnings` (Candidate-Brain-only, unchanged). This model
sits AROUND it: it references the original CV (never copied, never modified - see
`app.services.private_files`), the accepted contact if any (step 8, never a `pending`
observation), and the ADDITIONAL, clearly-separated personalisation context step 9 introduces
(`personalization_context`: company facts, contact framing, GitHub evidence) - kept apart from
`claims`/`selected_evidence` on purpose, so a Candidate Brain fact, a company fact and a GitHub
repository are never fused into one undifferentiated, unsourced assertion.

Content is immutable, exactly like `ApplicationDraft`: only `status`, `decided_at`, `decided_by`
and `superseded_by_id` may change, through an explicit human decision or a new `prepare()` call
superseding an undecided package (see `app.services.application_package`).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import ApplicationPackageStatus, enum_column
from app.models.immutability import make_immutable

# Columns that define what a package MEANS. They can never be updated.
IMMUTABLE_PACKAGE_COLUMNS = (
    "candidate_id",
    "target_id",
    "qualification_id",
    "draft_id",
    "cv_document_id",
    "contact_id",
    "personalization_context",
    "warnings",
    "inputs_fingerprint",
    "created_at",
)


class ApplicationPackage(CandidateOwnedMixin, Base):
    __tablename__ = "application_packages"
    __table_args__ = (
        CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)",
            name="decided_at_iff_decided",
        ),
        # One PENDING (undecided) package per target: a new `prepare()` supersedes it, never
        # leaves two packages competing for the same human decision.
        Index(
            "uq_application_packages_pending",
            "target_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'pending_validation')"),
            sqlite_where=text("status IN ('draft', 'pending_validation')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    # The qualification this package's requirement context was built from.
    qualification_id: Mapped[int | None] = mapped_column(
        ForeignKey("qualifications.id", ondelete="SET NULL")
    )
    # The generated text, produced by the existing, unmodified DraftService.generate().
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_drafts.id", ondelete="SET NULL")
    )
    # Reference only - the file itself lives in data/private/documents/, read-only, never touched.
    cv_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_ingestions.id", ondelete="SET NULL")
    )
    # An ACCEPTED contact only (never a pending ContactResearchObservation - enforced in the
    # service, not representable here since this column only ever points at a real Contact row).
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    status: Mapped[ApplicationPackageStatus] = mapped_column(
        enum_column(ApplicationPackageStatus), default=ApplicationPackageStatus.DRAFT
    )
    # {"company_evidence": [...], "contact": {...} | null, "github_evidence": [...]}: kept
    # strictly separate from ApplicationDraft.claims/selected_evidence (Candidate-Brain-only).
    personalization_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # [{"code", "text"}, ...]: e.g. no CV reference, no accepted contact, GitHub unavailable.
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Hash of everything that went into this package (qualification fingerprint, company facts,
    # accepted contact, GitHub evidence): the same idempotence/staleness mechanism as
    # `Qualification.inputs_fingerprint`, applied one layer up.
    inputs_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The only mutable part: a human decision (or being superseded by a new `prepare()` call).
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(16))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("application_packages.id"))


make_immutable(ApplicationPackage.__table__, IMMUTABLE_PACKAGE_COLUMNS)  # type: ignore[arg-type]


@event.listens_for(ApplicationPackage, "before_update")
def _refuse_content_change(
    mapper: Mapper[Any], connection: Any, target: ApplicationPackage
) -> None:
    state = inspect(target)
    for column in IMMUTABLE_PACKAGE_COLUMNS:
        if state.attrs[column].history.has_changes():
            raise RuntimeError("package content is immutable: prepare a new package instead")
