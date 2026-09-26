"""Search profiles and their criteria.

A `SearchProfile` is the operational description of what the candidate looks for; it is
distinct from `CandidatePreference`, the raw declaration it can be seeded from. Each
`SearchCriterion` has a level (required / preferred / flexible).

Criteria are IMMUTABLE: their content never changes once created, so that past qualifications
stay readable. A modification creates a new version and deactivates the old one (`superseded_by`).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    CriterionOrigin,
    ProfileOrigin,
    enum_column,
)
from app.models.immutability import make_immutable

# Columns that define what a criterion MEANS. They can never be updated.
IMMUTABLE_CRITERION_COLUMNS = (
    "profile_id",
    "dimension",
    "operator",
    "match_values",
    "level",
    "note",
    "origin",
    "origin_ref",
)


class SearchProfile(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "search_profiles"
    __table_args__ = (
        UniqueConstraint("candidate_id", "name"),
        # At most one active profile per candidate.
        Index(
            "uq_search_profiles_active",
            "candidate_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[ProfileOrigin] = mapped_column(
        enum_column(ProfileOrigin), default=ProfileOrigin.MANUAL
    )
    # Preferences/constraints that could not be turned into an evaluable criterion:
    # [{"source": "constraint:3", "code": "unsupported_constraint"}]. Codes only, no text.
    unmapped: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    criteria: Mapped[list["SearchCriterion"]] = relationship(
        lazy="selectin", order_by="SearchCriterion.id", viewonly=True
    )


class SearchCriterion(Base):
    __tablename__ = "search_criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[CriterionDimension] = mapped_column(enum_column(CriterionDimension))
    operator: Mapped[CriterionOperator] = mapped_column(enum_column(CriterionOperator))
    # Values compared with the target (already normalised by the API layer).
    match_values: Mapped[list[str]] = mapped_column(JSON)
    level: Mapped[CriterionLevel] = mapped_column(enum_column(CriterionLevel))
    note: Mapped[str | None] = mapped_column(String(255))
    origin: Mapped[CriterionOrigin] = mapped_column(
        enum_column(CriterionOrigin), default=CriterionOrigin.MANUAL
    )
    # Traceability of a seeded criterion: "preference:target_roles" or "constraint:12".
    origin_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The only mutable part: a criterion can be deactivated (and point to its successor).
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("search_criteria.id"))


make_immutable(SearchCriterion.__table__, IMMUTABLE_CRITERION_COLUMNS)  # type: ignore[arg-type]


@event.listens_for(SearchCriterion, "before_update")
def _refuse_content_change(mapper: Mapper[Any], connection: Any, target: SearchCriterion) -> None:
    state = inspect(target)
    for column in IMMUTABLE_CRITERION_COLUMNS:
        if state.attrs[column].history.has_changes():
            raise RuntimeError("search criteria are immutable: create a new version instead")
