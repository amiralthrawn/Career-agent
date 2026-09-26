from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.models.enums import ConstraintType, EmploymentType, RemotePreference
from app.schemas.common import LongText, ShortStr, TimestampedRead

StringList = list[ShortStr]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class PreferenceCreate(BaseModel):
    target_roles: StringList = Field(default_factory=list)
    contract_types: list[EmploymentType] = Field(default_factory=list)
    preferred_locations: StringList = Field(default_factory=list)
    remote_preference: RemotePreference | None = None
    target_sectors: StringList = Field(default_factory=list)
    target_domains: StringList = Field(default_factory=list)
    minimum_salary: int | None = Field(default=None, ge=0)
    preferred_salary: int | None = Field(default=None, ge=0)
    salary_currency: CurrencyCode | None = None
    target_companies: StringList = Field(default_factory=list)
    company_size_preferences: StringList = Field(default_factory=list)
    notes: LongText | None = None

    @model_validator(mode="after")
    def _salary_is_consistent(self) -> Self:
        if (
            self.minimum_salary is not None
            and self.preferred_salary is not None
            and self.preferred_salary < self.minimum_salary
        ):
            raise ValueError("preferred_salary must not be lower than minimum_salary")
        if (self.minimum_salary is not None or self.preferred_salary is not None) and (
            self.salary_currency is None
        ):
            raise ValueError("salary_currency is required when a salary is given")
        return self


class PreferenceRead(PreferenceCreate, TimestampedRead):
    pass


class ConstraintCreate(BaseModel):
    constraint_type: ConstraintType
    description: LongText
    value: dict[str, Any] | None = None
    is_hard: bool = True


class ConstraintRead(ConstraintCreate, TimestampedRead):
    pass
