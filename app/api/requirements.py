"""Requirement routes (step 3b): what a target's source asks for.

Extraction is an EXPLICIT call: nothing extracts requirements behind the caller's back.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.requirements import (
    ExtractionReport,
    ManualRequirementCreate,
    RequirementRead,
    RequirementWriteResult,
)
from app.services.requirements import RequirementService

router = APIRouter(prefix="/api/targets", tags=["requirements"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{target_id}/requirements", response_model=list[RequirementRead])
def list_requirements(
    target_id: int, session: DbSession, include_inactive: Annotated[bool, Query()] = False
) -> list[RequirementRead]:
    """Active requirements of a target (`include_inactive` adds superseded / retired versions)."""
    rows = RequirementService(session).list(target_id, include_inactive=include_inactive)
    return [RequirementRead.model_validate(row) for row in rows]


@router.post(
    "/{target_id}/requirements",
    response_model=RequirementWriteResult,
    status_code=status.HTTP_201_CREATED,
)
def add_requirement(
    target_id: int, data: ManualRequirementCreate, response: Response, session: DbSession
) -> RequirementWriteResult:
    """Record a requirement typed by the human (exact excerpt + source). Idempotent (200)."""
    requirement, created = RequirementService(session).add_manual(target_id, data)
    if not created:
        response.status_code = status.HTTP_200_OK
    return RequirementWriteResult(
        requirement=RequirementRead.model_validate(requirement), created=created
    )


@router.post("/{target_id}/requirements/extract", response_model=ExtractionReport)
def extract_requirements(target_id: int, session: DbSession) -> ExtractionReport:
    """Deterministically extract requirements from the offer text. Re-running creates nothing."""
    return RequirementService(session).extract(target_id, actor="api")
