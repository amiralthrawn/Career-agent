from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import SendBatchItemStatus, SendBatchStatus
from app.schemas.common import ORMModel, ShortStr


class SendBatchCreateRequest(BaseModel):
    application_package_ids: list[int] = Field(min_length=1, max_length=500)
    # Optional: a repeated create with the same key returns the existing batch (Part 9).
    idempotency_key: ShortStr | None = None


class SendBatchItemRead(ORMModel):
    id: int
    application_package_id: int
    status: SendBatchItemStatus
    failure_reason: str | None
    provider: str | None
    provider_message_id: str | None
    thread_id: str | None
    attempts: int
    sent_at: datetime | None


class SendBatchRead(BaseModel):
    id: int
    status: SendBatchStatus
    correlation_id: str
    created_by: str
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime
    items: list[SendBatchItemRead]
