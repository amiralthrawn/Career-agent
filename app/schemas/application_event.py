from datetime import datetime
from typing import Self

from pydantic import BaseModel, model_validator

from app.models.enums import ApplicationEventOrigin, ApplicationEventType, InfoStatus
from app.schemas.common import LongText, ORMModel, ShortStr


class ApplicationEventRead(ORMModel):
    id: int
    application_package_id: int
    event_type: ApplicationEventType
    origin: ApplicationEventOrigin
    status: InfoStatus
    occurred_at: datetime
    recorded_at: datetime
    reference: str | None
    note: str | None
    corrected_event_id: int | None
    actor: str
    created_at: datetime


class ApplicationEventCreate(BaseModel):
    """Manual entry only: `prepared`/`approved`/`sent` are recorded automatically and refused
    here (see `app.services.application_tracking._SYSTEM_ONLY_TYPES`)."""

    event_type: ApplicationEventType
    occurred_at: datetime
    status: InfoStatus = InfoStatus.FOUND
    note: LongText | None = None
    reference: ShortStr | None = None


class ApplicationEventCorrect(BaseModel):
    """At least one field must change - a correction that changes nothing is not a correction."""

    event_type: ApplicationEventType | None = None
    occurred_at: datetime | None = None
    status: InfoStatus | None = None
    note: LongText | None = None
    reference: ShortStr | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("nothing to correct")
        return self


class NeedingFollowUpRead(BaseModel):
    application_package_id: int
    target_id: int
