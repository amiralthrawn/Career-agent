"""Evidence: the proof that a piece of information about the candidate is real.

Design choice: an `EvidenceLink` row associates one Evidence with exactly one fact
(skill, project, experience, education, certification or language). Instead of a generic
polymorphic (target_type, target_id) pair - which cannot have foreign keys - the link table
has one nullable foreign key per fact type and a CHECK constraint enforcing that exactly one
is set. This keeps referential integrity and cascade deletes in the database.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import (
    Confidence,
    EvidenceTargetType,
    InformationState,
    SourceType,
    enum_column,
)

# Foreign key column of EvidenceLink for each kind of fact.
LINK_COLUMNS: dict[EvidenceTargetType, str] = {
    EvidenceTargetType.SKILL: "skill_id",
    EvidenceTargetType.PROJECT: "project_id",
    EvidenceTargetType.EXPERIENCE: "experience_id",
    EvidenceTargetType.EDUCATION: "education_id",
    EvidenceTargetType.CERTIFICATION: "certification_id",
    EvidenceTargetType.LANGUAGE: "language_id",
}


class Evidence(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(enum_column(SourceType))
    source_name: Mapped[str] = mapped_column(String(255))
    # URL, or path relative to the private data directory (never absolute, never committed).
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[Confidence] = mapped_column(enum_column(Confidence), default=Confidence.LOW)
    verified: Mapped[bool] = mapped_column(default=False)

    links: Mapped[list["EvidenceLink"]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan", passive_deletes=True
    )


class EvidenceLink(TimestampMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint(
            "(skill_id IS NOT NULL) + (project_id IS NOT NULL) + (experience_id IS NOT NULL)"
            " + (education_id IS NOT NULL) + (certification_id IS NOT NULL)"
            " + (language_id IS NOT NULL) = 1",
            name="exactly_one_target",
        ),
        UniqueConstraint("evidence_id", "skill_id"),
        UniqueConstraint("evidence_id", "project_id"),
        UniqueConstraint("evidence_id", "experience_id"),
        UniqueConstraint("evidence_id", "education_id"),
        UniqueConstraint("evidence_id", "certification_id"),
        UniqueConstraint("evidence_id", "language_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), index=True
    )
    education_id: Mapped[int | None] = mapped_column(
        ForeignKey("education.id", ondelete="CASCADE"), index=True
    )
    certification_id: Mapped[int | None] = mapped_column(
        ForeignKey("certifications.id", ondelete="CASCADE"), index=True
    )
    language_id: Mapped[int | None] = mapped_column(
        ForeignKey("languages.id", ondelete="CASCADE"), index=True
    )
    # Why this evidence supports the fact (e.g. "used in the data pipeline").
    note: Mapped[str | None] = mapped_column(Text)

    evidence: Mapped[Evidence] = relationship(back_populates="links", lazy="joined")

    @property
    def target_type(self) -> EvidenceTargetType:
        for target_type, column in LINK_COLUMNS.items():
            if getattr(self, column) is not None:
                return target_type
        raise ValueError("EvidenceLink has no target")

    @property
    def target_id(self) -> int:
        value: int = getattr(self, LINK_COLUMNS[self.target_type])
        return value


def derive_information_state(evidence: Iterable[Evidence]) -> InformationState:
    """State of a fact, computed from its evidence - never stored, never claimed by default.

    No evidence means UNKNOWN (the fact exists but is unsupported), never a negation.
    """
    items = list(evidence)
    if not items:
        return InformationState.UNKNOWN
    if any(item.verified for item in items):
        return InformationState.VERIFIED
    if any(item.confidence in (Confidence.MEDIUM, Confidence.HIGH) for item in items):
        return InformationState.KNOWN
    return InformationState.UNCERTAIN


class EvidenceBackedMixin:
    """Adds traceability helpers to facts. Subclasses declare `evidence_links`."""

    if TYPE_CHECKING:
        evidence_links: Mapped[list[EvidenceLink]]

    @property
    def evidence_ids(self) -> list[int]:
        return sorted(link.evidence_id for link in self.evidence_links)

    @property
    def state(self) -> InformationState:
        return derive_information_state(link.evidence for link in self.evidence_links)


__all__ = [
    "LINK_COLUMNS",
    "Evidence",
    "EvidenceBackedMixin",
    "EvidenceLink",
    "derive_information_state",
]
