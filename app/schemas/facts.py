from pydantic import BaseModel, computed_field, model_validator

from app.models.enums import EducationStatus, EmploymentType, LanguageLevel, SkillLevel
from app.models.partial_date import DatePrecision, is_before, precision_of
from app.schemas.common import (
    DateRangeBase,
    DateRangePrecisionRead,
    EvidenceBackedRead,
    HttpUrlStr,
    LongText,
    PartialDateStr,
    ShortStr,
)


class EducationCreate(DateRangeBase):
    institution: ShortStr
    degree: ShortStr | None = None
    field_of_study: ShortStr | None = None
    description: LongText | None = None
    status: EducationStatus | None = None


class EducationRead(EducationCreate, EvidenceBackedRead, DateRangePrecisionRead):
    pass


class ExperienceCreate(DateRangeBase):
    company: ShortStr
    title: ShortStr
    description: LongText | None = None
    employment_type: EmploymentType | None = None


class ExperienceRead(ExperienceCreate, EvidenceBackedRead, DateRangePrecisionRead):
    pass


class ProjectCreate(DateRangeBase):
    name: ShortStr
    description: LongText | None = None
    url: HttpUrlStr | None = None
    repository_url: HttpUrlStr | None = None
    domain: ShortStr | None = None


class ProjectRead(ProjectCreate, EvidenceBackedRead, DateRangePrecisionRead):
    pass


class SkillCreate(BaseModel):
    """`level` is optional and stays empty unless the candidate states it explicitly."""

    name: ShortStr
    category: ShortStr | None = None
    level: SkillLevel | None = None
    description: LongText | None = None


class SkillRead(SkillCreate, EvidenceBackedRead):
    pass


class CertificationCreate(BaseModel):
    name: ShortStr
    issuer: ShortStr | None = None
    issue_date: PartialDateStr | None = None
    expiration_date: PartialDateStr | None = None
    credential_url: HttpUrlStr | None = None
    description: LongText | None = None

    @model_validator(mode="after")
    def _expiration_not_before_issue(self) -> "CertificationCreate":
        if (
            self.issue_date
            and self.expiration_date
            and is_before(self.expiration_date, self.issue_date)
        ):
            raise ValueError("expiration_date must not be before issue_date")
        return self


class CertificationRead(CertificationCreate, EvidenceBackedRead):
    @computed_field  # type: ignore[prop-decorator]
    @property
    def issue_date_precision(self) -> DatePrecision | None:
        return precision_of(self.issue_date)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expiration_date_precision(self) -> DatePrecision | None:
        return precision_of(self.expiration_date)


class LanguageCreate(BaseModel):
    language: ShortStr
    level: LanguageLevel | None = None


class LanguageRead(LanguageCreate, EvidenceBackedRead):
    pass
