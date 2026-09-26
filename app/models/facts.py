"""FACTS about the candidate. Each fact can be backed by one or more Evidence.

Dates are partial dates (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`): the precision of the source is kept.
"""

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import (
    EducationStatus,
    EmploymentType,
    LanguageLevel,
    SkillLevel,
    enum_column,
)
from app.models.evidence import EvidenceBackedMixin, EvidenceLink
from app.models.partial_date import PartialDateType


class Education(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "education"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution: Mapped[str] = mapped_column(String(255))
    degree: Mapped[str | None] = mapped_column(String(255))
    field_of_study: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[str | None] = mapped_column(PartialDateType)
    end_date: Mapped[str | None] = mapped_column(PartialDateType)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EducationStatus | None] = mapped_column(enum_column(EducationStatus))

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.education_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Experience(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[str | None] = mapped_column(PartialDateType)
    end_date: Mapped[str | None] = mapped_column(PartialDateType)
    description: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[EmploymentType | None] = mapped_column(enum_column(EmploymentType))

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.experience_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Project(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(2048))
    repository_url: Mapped[str | None] = mapped_column(String(2048))
    domain: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[str | None] = mapped_column(PartialDateType)
    end_date: Mapped[str | None] = mapped_column(PartialDateType)

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.project_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Skill(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    """A skill. `level` stays NULL until established; it is never inferred automatically."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("candidate_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(100))
    level: Mapped[SkillLevel | None] = mapped_column(enum_column(SkillLevel))
    description: Mapped[str | None] = mapped_column(Text)

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.skill_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Certification(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str | None] = mapped_column(String(255))
    issue_date: Mapped[str | None] = mapped_column(PartialDateType)
    expiration_date: Mapped[str | None] = mapped_column(PartialDateType)
    credential_url: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(Text)

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.certification_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Language(EvidenceBackedMixin, TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "languages"
    __table_args__ = (UniqueConstraint("candidate_id", "language"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(100))
    level: Mapped[LanguageLevel | None] = mapped_column(enum_column(LanguageLevel))

    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        foreign_keys=[EvidenceLink.language_id],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
