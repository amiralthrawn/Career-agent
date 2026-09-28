"""Schemas of search profiles and criteria. Criteria are immutable: a change is a new version."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.core.normalize import normalize_text
from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    CriterionOrigin,
    EmploymentType,
    ProfileOrigin,
    RemoteMode,
)
from app.schemas.common import ORMModel, ShortStr

MAX_VALUES = 200  # the "Criteria v1" keyword criterion lists every job-family/adjacent term
MAX_VALUE_CHARS = 100

Value = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_VALUE_CHARS)
]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
_CONTRACTS = {member.value for member in EmploymentType}
_REMOTE_MODES = {member.value for member in RemoteMode}


def normalize_values(dimension: CriterionDimension, values: list[str]) -> list[str]:
    """Validate and normalise criterion values (closed vocabularies are checked strictly)."""
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if dimension is CriterionDimension.CONTRACT_TYPE:
            value = value.lower()
            if value not in _CONTRACTS:
                raise ValueError(f"unknown contract type: expected one of {sorted(_CONTRACTS)}")
        elif dimension is CriterionDimension.REMOTE_MODE:
            value = value.lower()
            if value not in _REMOTE_MODES:
                raise ValueError(f"unknown remote mode: expected one of {sorted(_REMOTE_MODES)}")
        elif dimension is CriterionDimension.COUNTRY:
            value = value.upper()
            if len(value) != 2 or not value.isalpha():
                raise ValueError("countries are 2-letter codes")
        elif not normalize_text(value):
            raise ValueError("a value has no usable characters")
        if value not in result:
            result.append(value)
    return result


class CriterionInput(BaseModel):
    dimension: CriterionDimension
    operator: CriterionOperator = CriterionOperator.ANY_OF
    values: Annotated[list[Value], Field(min_length=1, max_length=MAX_VALUES)]
    level: CriterionLevel
    note: Note | None = None
    # Modify = create a new version of this criterion (same profile) and deactivate the old one.
    replaces: int | None = None

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        self.values = normalize_values(self.dimension, self.values)
        return self


class SearchProfileCreate(BaseModel):
    name: ShortStr
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = (
        None
    )
    # None = activate only if the candidate has no active profile yet.
    is_active: bool | None = None
    criteria: list[CriterionInput] = Field(default_factory=list)


class FromPreferencesRequest(BaseModel):
    name: ShortStr
    is_active: bool | None = None
    # Override the level given to PREFERENCE-derived criteria (constraints follow `is_hard`).
    levels: dict[CriterionDimension, CriterionLevel] = Field(default_factory=dict)


class SearchCriterionRead(ORMModel):
    id: int
    profile_id: int
    dimension: CriterionDimension
    operator: CriterionOperator
    values: list[str] = Field(validation_alias="match_values")
    level: CriterionLevel
    note: str | None
    origin: CriterionOrigin
    origin_ref: str | None
    active: bool
    deactivated_at: datetime | None
    superseded_by_id: int | None
    created_at: datetime


class UnmappedItem(BaseModel):
    """A preference or constraint that could not become an evaluable criterion (codes only)."""

    source: str
    code: str


class SearchProfileRead(ORMModel):
    id: int
    candidate_id: int
    name: str
    description: str | None
    is_active: bool
    origin: ProfileOrigin
    unmapped: list[UnmappedItem]
    criteria: list[SearchCriterionRead]  # active and past versions, oldest first
    created_at: datetime
    updated_at: datetime
