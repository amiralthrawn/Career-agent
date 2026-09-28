"""Schemas of campaigns (chained, bounded sourcing toward a daily target) and their funnel view.

No score anywhere: every figure below is a plain count of real rows (targets, qualifications,
contacts, drafts, packages), never a percentage or a ranking.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.integrations.sourcing.ports import MAX_RESULTS_LIMIT
from app.models.enums import ApplicationPackageStatus, CampaignStatus, SourcingMode
from app.schemas.common import ORMModel
from app.schemas.sourcing import ProviderId

MAX_DAILY_TARGET = 2000
MAX_CALLS_CEILING = 200
MAX_DURATION_CEILING_MINUTES = 480  # 8h: a human-scale, single-sitting or single-day ceiling


class CampaignCreate(BaseModel):
    """Every limit below is a REQUIRED, explicit safety cap - none of them defaults to
    "unbounded"."""

    profile_id: int | None = None  # default: the active profile
    mode: SourcingMode = SourcingMode.ALL
    provider: ProviderId
    daily_target: Annotated[int, Field(ge=1, le=MAX_DAILY_TARGET)] = 500
    max_calls: Annotated[int, Field(ge=1, le=MAX_CALLS_CEILING)] = 20
    max_duration_minutes: Annotated[int, Field(ge=1, le=MAX_DURATION_CEILING_MINUTES)] = 60
    max_results_per_call: Annotated[int, Field(ge=1, le=MAX_RESULTS_LIMIT)] = 20
    # Consecutive searches with zero new targets before concluding nothing more is reasonably
    # left to find right now.
    max_consecutive_empty: Annotated[int, Field(ge=1, le=20)] = 3


class CampaignRead(ORMModel):
    id: int
    candidate_id: int
    profile_id: int
    mode: SourcingMode
    provider: str
    daily_target: int
    max_calls: int
    max_duration_minutes: int
    max_consecutive_empty: int
    max_results_per_call: int
    status: CampaignStatus
    stop_reason: str | None
    started_at: datetime
    finished_at: datetime | None
    calls_made: int
    consecutive_empty_calls: int
    targets_created: int
    requirements_extracted: int
    contacts_proposed: int
    drafts_generated: int
    packages_prepared: int


class CampaignFunnel(BaseModel):
    """Computed fresh at read time from the targets this campaign's rounds produced - never a
    duplicated, drift-prone copy of the qualification/contact/package tables' own truth.

    `contacts_proposed`/`contacts_accepted` reuse the existing, human-in-the-loop
    `ContactResearchObservation` lifecycle: a PROPOSED contact is a claim, never an address to
    use; only `contacts_accepted_with_email` (a real, human-confirmed `ContactChannel`) is
    something a draft could actually be sent to. Nothing here is invented from free text.
    """

    campaign: CampaignRead
    raw_results_total: int
    targets_total: int
    qualified: int
    uncertain: int
    excluded: int
    contacts_proposed: int
    contacts_accepted_with_email: int
    packages_by_status: dict[ApplicationPackageStatus, int]
    packages_sent: int
