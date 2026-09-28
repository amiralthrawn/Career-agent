"""Qualification Criteria v1 routes (step 3a) - see docs/qualification_v1.md.

Thin HTTP layer only: every route delegates to `OpportunityQualificationService`, the SAME
service the CLI uses (`app.cli.qualification`) - no business rule is duplicated here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.opportunity_qualification import V1OpportunitySummary, V1QualificationRead
from app.services.opportunity_qualification import OpportunityQualificationService

router = APIRouter(prefix="/api/opportunities", tags=["opportunity-qualification"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/{target_id}/qualification",
    response_model=V1QualificationRead,
    status_code=status.HTTP_201_CREATED,
)
def run_qualification(
    target_id: int, response: Response, session: DbSession
) -> V1QualificationRead:
    """Qualify a target against "Criteria v1". Idempotent: unchanged inputs return the existing
    qualification (200) instead of a new one (201)."""
    outcome = OpportunityQualificationService(session).run(target_id)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return outcome.qualification


@router.get("/{target_id}/qualification", response_model=V1QualificationRead)
def get_qualification(target_id: int, session: DbSession) -> V1QualificationRead:
    """The latest "Criteria v1" qualification of a target, with `stale`."""
    return OpportunityQualificationService(session).show(target_id)


@router.get("/qualified", response_model=list[V1OpportunitySummary])
def list_qualified(session: DbSession) -> list[V1OpportunitySummary]:
    return list(OpportunityQualificationService(session).list_qualified())


@router.get("/uncertain", response_model=list[V1OpportunitySummary])
def list_uncertain(session: DbSession) -> list[V1OpportunitySummary]:
    return list(OpportunityQualificationService(session).list_uncertain())
