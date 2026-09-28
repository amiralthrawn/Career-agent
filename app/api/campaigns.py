"""Campaign routes (read-only): consult progress/funnel from anywhere while a campaign runs.

Starting a campaign is CLI-only (`career-agent sourcing campaign start`), deliberately: a
campaign can run for its full configured duration (up to `max_duration_minutes`), and this
project has no background worker/queue to run it off an HTTP request without blocking one. The
CLI IS the long-running foreground process a human explicitly starts and watches; these routes
only let a human (or a script) check on it meanwhile, from anywhere else.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.sourcing.ports import default_providers
from app.schemas.campaign import CampaignFunnel, CampaignRead
from app.services.campaign import CampaignService

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

DbSession = Annotated[Session, Depends(get_db)]


def _service(session: DbSession) -> CampaignService:
    # Read-only here: no provider is ever called, so an empty registry is always sufficient.
    return CampaignService(session, default_providers())


@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 25
) -> Sequence[CampaignRead]:
    return [CampaignRead.model_validate(c) for c in _service(session).list(limit=limit)]


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(campaign_id: int, session: DbSession) -> CampaignRead:
    return CampaignRead.model_validate(_service(session).get(campaign_id))


@router.get("/{campaign_id}/funnel", response_model=CampaignFunnel)
def get_campaign_funnel(campaign_id: int, session: DbSession) -> CampaignFunnel:
    return _service(session).funnel(campaign_id)
