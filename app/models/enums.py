"""Controlled vocabularies shared by models and schemas.

Stored as plain VARCHAR (not native PostgreSQL enums) so that adding a value never
requires an `ALTER TYPE` migration.
"""

from enum import StrEnum

from sqlalchemy import Enum


def enum_column[E: StrEnum](enum_cls: type[E]) -> Enum:
    """SQLAlchemy column type storing the enum *values* in a VARCHAR."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class SourceType(StrEnum):
    CV = "cv"
    GITHUB = "github"
    PORTFOLIO = "portfolio"
    DIPLOMA = "diploma"
    CERTIFICATION = "certification"
    COVER_LETTER = "cover_letter"
    DOCUMENT = "document"
    LINKEDIN_EXPORT = "linkedin_export"
    OTHER = "other"


class Confidence(StrEnum):
    """How much the candidate/system trusts an evidence. Not an AI score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InformationState(StrEnum):
    """Derived (never stored) state of a fact, computed from its evidence.

    Every state describes a fact that EXISTS in the Brain. There is deliberately no "absent",
    "false" or "negative" member: absence of information is not negative information, and a
    missing fact (no row) is simply not known.
    """

    UNKNOWN = "unknown"  # fact exists, no usable evidence (NOT "false")
    UNCERTAIN = "uncertain"  # only low-confidence, unverified evidence
    KNOWN = "known"  # medium/high-confidence evidence, not verified
    VERIFIED = "verified"  # at least one verified evidence


class EvidenceTargetType(StrEnum):
    SKILL = "skill"
    PROJECT = "project"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"


class ProposalStatus(StrEnum):
    """Lifecycle of an ingestion proposal. Only a human decision leaves `pending`."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class LanguageLevel(StrEnum):
    A1 = "a1"
    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1 = "c1"
    C2 = "c2"
    NATIVE = "native"


class EducationStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class EmploymentType(StrEnum):
    """Used for past experiences and for contract preferences."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    APPRENTICESHIP = "apprenticeship"
    FIXED_TERM = "fixed_term"
    FREELANCE = "freelance"
    VOLUNTEER = "volunteer"
    OTHER = "other"


class RemotePreference(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    NO_PREFERENCE = "no_preference"


class ConstraintType(StrEnum):
    GEOGRAPHIC = "geographic"
    CONTRACT = "contract"
    AVAILABILITY = "availability"
    SALARY = "salary"
    SCHEDULE = "schedule"
    OTHER = "other"
