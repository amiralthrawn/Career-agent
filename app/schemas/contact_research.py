from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ChannelKind, ProposalStatus, RoleCategory
from app.schemas.common import LongText, ORMModel, ShortStr


class ContactSearchRequest(BaseModel):
    """Search Perplexity for a contact of each listed category, for one target."""

    role_categories: list[RoleCategory] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _known_categories_only(self) -> Self:
        if RoleCategory.UNKNOWN in self.role_categories:
            raise ValueError("unknown is not a searchable category")
        if len(set(self.role_categories)) != len(self.role_categories):
            raise ValueError("role_categories must not repeat")
        return self


class ContactObservationRead(ORMModel):
    id: int
    target_id: int
    company_id: int
    requested_role_category: RoleCategory
    status: ProposalStatus
    claim: str
    source_url: str
    source_title: str | None
    excerpt: str | None
    published: str | None
    retrieved_at: datetime
    reviewed_data: dict[str, Any] | None
    review_note: str | None
    decided_at: datetime | None
    resulting_contact_id: int | None
    created_at: datetime


class ContactObservationAccept(BaseModel):
    """Human confirmation of the person/channel to record - never auto-filled from `claim` or
    `excerpt`. Mirrors `app.schemas.ingestion.ProposalAccept`: acceptance is not independent
    verification, so the resulting contact is stored `verified=False`, exactly like a CV fact.
    """

    full_name: ShortStr | None = None
    is_generic: bool = False
    role_title: ShortStr | None = None
    # Defaults to the category this observation was searched for; may be corrected by the human.
    role_category: RoleCategory | None = None
    channel_kind: ChannelKind | None = None
    channel_value: ShortStr | None = None
    note: LongText | None = None

    @model_validator(mode="after")
    def _named_or_generic(self) -> Self:
        if not self.full_name and not self.is_generic:
            raise ValueError("give full_name, or is_generic=true for a shared mailbox")
        if (self.channel_kind is None) != (self.channel_value is None):
            raise ValueError("channel_kind and channel_value must be given together")
        return self


class ContactObservationReject(BaseModel):
    note: LongText | None = None


# --- Batch (Part 6) ---------------------------------------------------------------------------


class ContactBatchRequest(BaseModel):
    target_ids: list[int] | None = None
    max_targets: int | None = Field(default=None, ge=1)
    role_categories: list[RoleCategory] | None = None
    max_research_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _known_categories_only(self) -> Self:
        if self.role_categories is not None and RoleCategory.UNKNOWN in self.role_categories:
            raise ValueError("unknown is not a searchable category")
        return self


class ContactBatchItemRead(BaseModel):
    company_id: int
    role_category: RoleCategory
    targets_affected: list[int]
    outcome: str
    observations_created: int


class ContactBatchReportRead(BaseModel):
    targets_processed: int
    searches_needed: int
    searches_researched: int
    searches_failed: int
    searches_skipped_budget: int
    searches_skipped_already_known: int
    observations_created: int
    items: list[ContactBatchItemRead]
