"""Qualification of a target against a search profile: explainable, deterministic, immutable.

A qualification is decision support. It contains NO score, NO percentage and NO weight: only a
status, one result per criterion, and reasons that reference those results by id.

Rows are append-only: a qualification is never edited. When the inputs change, a NEW
qualification is computed; the old one stays as history and is reported as stale (its
`inputs_fingerprint` no longer matches the current inputs). Staleness is derived, never stored.
"""

from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import (
    CriterionOutcome,
    EvaluationCode,
    QualificationMethod,
    QualificationStatus,
    ReasonCode,
    enum_column,
)
from app.models.immutability import make_immutable
from app.models.requirements import RequirementMatch
from app.models.search import SearchCriterion


class Qualification(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "qualifications"
    __table_args__ = (Index("ix_qualifications_target_profile", "target_id", "profile_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("search_profiles.id", ondelete="CASCADE"))
    method: Mapped[QualificationMethod] = mapped_column(
        enum_column(QualificationMethod), default=QualificationMethod.DETERMINISTIC
    )
    # Hash of everything the evaluation used (criteria + target data + evaluator version).
    inputs_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[QualificationStatus] = mapped_column(enum_column(QualificationStatus))

    results: Mapped[list["CriterionResult"]] = relationship(
        lazy="selectin", order_by="CriterionResult.id", viewonly=True
    )
    reasons: Mapped[list["QualificationReason"]] = relationship(
        lazy="selectin", order_by="QualificationReason.position", viewonly=True
    )
    # What the Candidate Brain establishes about each requirement (step 3b). Same immutability.
    requirement_matches: Mapped[list[RequirementMatch]] = relationship(
        lazy="selectin", order_by="RequirementMatch.id", viewonly=True
    )


class CriterionResult(Base):
    __tablename__ = "criterion_results"
    __table_args__ = (UniqueConstraint("qualification_id", "criterion_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    qualification_id: Mapped[int] = mapped_column(
        ForeignKey("qualifications.id", ondelete="CASCADE"), index=True
    )
    criterion_id: Mapped[int] = mapped_column(ForeignKey("search_criteria.id"), index=True)
    outcome: Mapped[CriterionOutcome] = mapped_column(enum_column(CriterionOutcome))
    code: Mapped[EvaluationCode] = mapped_column(enum_column(EvaluationCode))
    # The single value of the target that was compared (e.g. a country code), never a long text.
    observed: Mapped[str | None] = mapped_column(String(255))

    criterion: Mapped[SearchCriterion] = relationship(lazy="joined", viewonly=True)


class QualificationReason(Base):
    """A reason, stored as a code and a reference to an existing result: never free text."""

    __tablename__ = "qualification_reasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    qualification_id: Mapped[int] = mapped_column(
        ForeignKey("qualifications.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    code: Mapped[ReasonCode] = mapped_column(enum_column(ReasonCode))
    # The criterion result this reason is about (null for target-level reasons).
    criterion_result_id: Mapped[int | None] = mapped_column(ForeignKey("criterion_results.id"))
    origin: Mapped[QualificationMethod] = mapped_column(
        enum_column(QualificationMethod), default=QualificationMethod.DETERMINISTIC
    )


for _model in (Qualification, CriterionResult, QualificationReason):
    make_immutable(_model.__table__)  # type: ignore[arg-type]


@event.listens_for(Qualification, "before_update")
@event.listens_for(CriterionResult, "before_update")
@event.listens_for(QualificationReason, "before_update")
def _refuse_update(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise RuntimeError("qualification rows are immutable: compute a new qualification")
