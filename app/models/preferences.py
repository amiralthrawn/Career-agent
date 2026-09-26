"""PREFERENCES (what the candidate wants) and CONSTRAINTS (what limits the candidate).

Neither is a fact: they are declarations by the candidate, so they carry no Evidence and
must never be used to claim a skill or an experience.
"""

from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import ConstraintType, RemotePreference, enum_column


class CandidatePreference(TimestampMixin, Base):
    """At most one row per candidate."""

    __tablename__ = "candidate_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True
    )
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Values of `EmploymentType`.
    contract_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    remote_preference: Mapped[RemotePreference | None] = mapped_column(
        enum_column(RemotePreference)
    )
    target_sectors: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Annual gross amounts, in `salary_currency` (ISO 4217, e.g. EUR).
    minimum_salary: Mapped[int | None]
    preferred_salary: Mapped[int | None]
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    target_companies: Mapped[list[str]] = mapped_column(JSON, default=list)
    company_size_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class CandidateConstraint(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "candidate_constraints"

    id: Mapped[int] = mapped_column(primary_key=True)
    constraint_type: Mapped[ConstraintType] = mapped_column(enum_column(ConstraintType))
    description: Mapped[str] = mapped_column(Text)
    # Optional machine-readable form, e.g. {"max_distance_km": 30}. Free-form on purpose.
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Hard constraint = must never be violated; soft = preferably respected.
    is_hard: Mapped[bool] = mapped_column(default=True)
