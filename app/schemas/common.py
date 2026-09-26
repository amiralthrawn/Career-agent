from datetime import datetime
from typing import Annotated, Self
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    computed_field,
    model_validator,
)

from app.models.enums import InformationState
from app.models.partial_date import (
    DatePrecision,
    is_before,
    normalize_partial_date,
    precision_of,
)

ShortStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]


def _check_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an http(s) URL")
    return value


HttpUrlStr = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2048), AfterValidator(_check_http_url)
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TimestampedRead(ORMModel):
    id: int
    candidate_id: int
    created_at: datetime
    updated_at: datetime


class EvidenceBackedRead(TimestampedRead):
    """Read schema of a fact: exposes how well it is backed by evidence."""

    state: InformationState
    evidence_ids: list[int]


# A date keeps the precision of its source: "2024", "2024-06" or "2024-06-15".
# A missing month or day is never completed.
PartialDateStr = Annotated[str, AfterValidator(normalize_partial_date)]


class DateRangeBase(BaseModel):
    start_date: PartialDateStr | None = None
    end_date: PartialDateStr | None = None

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.start_date and self.end_date and is_before(self.end_date, self.start_date):
            raise ValueError("end_date must not be before start_date")
        return self


class DateRangePrecisionRead(BaseModel):
    """Read-only precision of `start_date` / `end_date` (`year`, `month`, `day`, or null)."""

    start_date: str | None = None
    end_date: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_date_precision(self) -> DatePrecision | None:
        return precision_of(self.start_date)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def end_date_precision(self) -> DatePrecision | None:
        return precision_of(self.end_date)
