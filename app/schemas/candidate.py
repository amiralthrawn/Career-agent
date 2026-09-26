from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.schemas.common import LongText, ORMModel, ShortStr

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50)]


class CandidateCreate(BaseModel):
    first_name: ShortStr
    last_name: ShortStr
    email: Email | None = None
    phone: Phone | None = None
    headline: ShortStr | None = None
    summary: LongText | None = None
    location: ShortStr | None = None
    availability: ShortStr | None = None


class CandidateRead(CandidateCreate, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime
