"""Schemas of sourcing runs. Counters and codes only: no provider payload, no secret."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.integrations.sourcing.ports import MAX_RESULTS_LIMIT
from app.models.enums import (
    ItemReason,
    ProviderKind,
    SearchRunItemOutcome,
    SearchRunStatus,
    SourcingMode,
)
from app.schemas.common import ORMModel

ProviderId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
]


class SearchRunCreate(BaseModel):
    """Start a search. Everything here is validated BEFORE any provider is called."""

    mode: SourcingMode
    provider: ProviderId
    profile_id: int | None = None  # default: the active profile
    max_results: Annotated[int, Field(ge=1, le=MAX_RESULTS_LIMIT)] = 20


class SearchRunItemRead(ORMModel):
    position: int
    outcome: SearchRunItemOutcome
    reason: ItemReason | None
    fields: list[str]
    target_id: int | None
    qualification_id: int | None
    source_url: str | None
    excerpt: str | None


class SearchRunErrorRead(BaseModel):
    code: str
    detail: str | None = None


class SearchRunRead(ORMModel):
    id: int
    profile_id: int
    mode: SourcingMode
    provider: str
    provider_kind: ProviderKind
    status: SearchRunStatus
    query: dict[str, Any]
    max_results: int
    started_at: datetime
    finished_at: datetime | None
    # Raw counters, never combined into a score.
    results_raw: int
    targets_created: int
    targets_existing: int
    companies_created: int
    opportunities_created: int
    qualifications_created: int
    items_rejected: int
    item_errors: int
    errors: list[SearchRunErrorRead]
    sources_consulted: list[str]
    # `mode=all` only: the SAME counter names, split by which flow an item became. Empty
    # otherwise - never a second set of counters to reconcile with the aggregate above.
    breakdown: dict[str, dict[str, int]]


class SearchRunDetail(SearchRunRead):
    items: list[SearchRunItemRead]
