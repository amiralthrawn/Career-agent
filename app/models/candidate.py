from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.evidence import Evidence
    from app.models.facts import Certification, Education, Experience, Language, Project, Skill
    from app.models.preferences import CandidateConstraint, CandidatePreference


class Candidate(TimestampMixin, Base):
    """The main candidate. Identity data only; never hardcode real values in code."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    headline: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    availability: Mapped[str | None] = mapped_column(String(255))

    education: Mapped[list["Education"]] = relationship(cascade="all, delete-orphan")
    experiences: Mapped[list["Experience"]] = relationship(cascade="all, delete-orphan")
    projects: Mapped[list["Project"]] = relationship(cascade="all, delete-orphan")
    skills: Mapped[list["Skill"]] = relationship(cascade="all, delete-orphan")
    certifications: Mapped[list["Certification"]] = relationship(cascade="all, delete-orphan")
    languages: Mapped[list["Language"]] = relationship(cascade="all, delete-orphan")
    preferences: Mapped["CandidatePreference | None"] = relationship(
        cascade="all, delete-orphan", uselist=False
    )
    constraints: Mapped[list["CandidateConstraint"]] = relationship(cascade="all, delete-orphan")
    evidence: Mapped[list["Evidence"]] = relationship(cascade="all, delete-orphan")
