"""Requirements of a target, and how the Candidate Brain matches them (step 3b).

- `TargetRequirement`: something the SOURCE asks for (a skill, an explicit duration of
  experience). It belongs to a target (not to an offer, so a spontaneous target may have manual
  requirements only) and always keeps where it comes from: an origin, a `Source`, the exact
  excerpt, and the hash of the text it was read from.
- `RequirementMatch` / `RequirementMatchFact`: what the Brain establishes about one requirement
  at the time of one qualification, with the facts (Skill / Project / Experience) it rests on.

Everything is append-only. A requirement never changes its content: a correction creates a new
version and deactivates the old one (`superseded_by_id`), like search criteria. Matches belong to
an immutable qualification and are immutable themselves.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base, CandidateOwnedMixin
from app.models.enums import (
    EvidenceTargetType,
    InformationState,
    MatchFactRole,
    MatchNote,
    MatchStatus,
    RequirementImportance,
    RequirementKind,
    RequirementOrigin,
    enum_column,
)
from app.models.facts import Experience, Project, Skill
from app.models.immutability import make_immutable
from app.models.sources import Source

# Columns that define what a requirement MEANS. They can never be updated.
IMMUTABLE_REQUIREMENT_COLUMNS = (
    "candidate_id",
    "target_id",
    "kind",
    "key",
    "label",
    "importance",
    "origin",
    "source_field",
    "source_id",
    "source_hash",
    "excerpt",
    "value",
    "qualifier",
    "extractor_version",
    "created_at",
)

MATCH_FACT_COLUMNS: dict[EvidenceTargetType, str] = {
    EvidenceTargetType.SKILL: "skill_id",
    EvidenceTargetType.PROJECT: "project_id",
    EvidenceTargetType.EXPERIENCE: "experience_id",
}


class TargetRequirement(CandidateOwnedMixin, Base):
    __tablename__ = "target_requirements"
    __table_args__ = (
        CheckConstraint("length(excerpt) > 0", name="excerpt_not_empty"),
        # One ACTIVE requirement per (target, kind, key): a re-extraction never duplicates.
        Index(
            "uq_target_requirements_active",
            "target_id",
            "kind",
            "key",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[RequirementKind] = mapped_column(enum_column(RequirementKind))
    # Taxonomy key ("python") or normalised label; "experience_years:2" for a duration.
    key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(255))
    # Only what the source says; `unspecified` otherwise. Never inferred.
    importance: Mapped[RequirementImportance] = mapped_column(enum_column(RequirementImportance))
    origin: Mapped[RequirementOrigin] = mapped_column(enum_column(RequirementOrigin))
    # Where in the source: "opportunity.description_text" or "manual".
    source_field: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    # sha256 of the text that was read (offer description) or of the excerpt (manual).
    source_hash: Mapped[str] = mapped_column(String(64))
    # EXACT text of the source. Never generated, never reformulated.
    excerpt: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(String(64))  # experience, as written: "2+ years"
    qualifier: Mapped[str | None] = mapped_column(String(255))  # experience scope, as written
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The only mutable part: a requirement can be deactivated (and point to its successor).
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("target_requirements.id"))

    source: Mapped[Source] = relationship(lazy="joined", viewonly=True)


class RequirementMatch(Base):
    __tablename__ = "requirement_matches"
    __table_args__ = (UniqueConstraint("qualification_id", "requirement_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    qualification_id: Mapped[int] = mapped_column(
        ForeignKey("qualifications.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("target_requirements.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[MatchStatus] = mapped_column(enum_column(MatchStatus))
    note: Mapped[MatchNote] = mapped_column(enum_column(MatchNote))

    requirement: Mapped[TargetRequirement] = relationship(lazy="joined", viewonly=True)
    facts: Mapped[list["RequirementMatchFact"]] = relationship(
        lazy="selectin", order_by="RequirementMatchFact.id", viewonly=True
    )


class RequirementMatchFact(Base):
    """A Brain fact a match rests on (one foreign key per kind, exactly one set)."""

    __tablename__ = "requirement_match_facts"
    __table_args__ = (
        CheckConstraint(
            "(skill_id IS NOT NULL) + (project_id IS NOT NULL) + (experience_id IS NOT NULL) = 1",
            name="exactly_one_fact",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_matches.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MatchFactRole] = mapped_column(enum_column(MatchFactRole))
    # The evidence state of the fact WHEN the match was computed (states are derived, not stored).
    state: Mapped[InformationState] = mapped_column(enum_column(InformationState))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE")
    )

    skill: Mapped[Skill | None] = relationship(lazy="joined", viewonly=True)
    project: Mapped[Project | None] = relationship(lazy="joined", viewonly=True)
    experience: Mapped[Experience | None] = relationship(lazy="joined", viewonly=True)

    @property
    def fact_name(self) -> str:
        """Readable name of the referenced Brain fact (never stored: the fact owns it)."""
        if self.skill is not None:
            return self.skill.name
        if self.project is not None:
            return self.project.name
        if self.experience is not None:
            return self.experience.title
        raise ValueError("RequirementMatchFact has no fact")

    @property
    def fact_type(self) -> EvidenceTargetType:
        for fact_type, column in MATCH_FACT_COLUMNS.items():
            if getattr(self, column) is not None:
                return fact_type
        raise ValueError("RequirementMatchFact has no fact")

    @property
    def fact_id(self) -> int:
        value: int = getattr(self, MATCH_FACT_COLUMNS[self.fact_type])
        return value


make_immutable(TargetRequirement.__table__, IMMUTABLE_REQUIREMENT_COLUMNS)  # type: ignore[arg-type]
for _model in (RequirementMatch, RequirementMatchFact):
    make_immutable(_model.__table__)  # type: ignore[arg-type]


@event.listens_for(TargetRequirement, "before_update")
def _refuse_content_change(mapper: Mapper[Any], connection: Any, target: TargetRequirement) -> None:
    state = inspect(target)
    for column in IMMUTABLE_REQUIREMENT_COLUMNS:
        if state.attrs[column].history.has_changes():
            raise RuntimeError("requirements are immutable: create a new version instead")


@event.listens_for(RequirementMatch, "before_update")
@event.listens_for(RequirementMatchFact, "before_update")
def _refuse_update(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise RuntimeError("requirement matches are immutable: compute a new qualification")
