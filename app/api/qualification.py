"""Qualification routes (deterministic criteria evaluation, step 3a).

A qualification is decision support (excluded / needs_information / candidate), never a score.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.qualification import (
    QualificationRead,
    QualifyRequest,
    QualifyResult,
    RunReport,
    RunRequest,
    read_qualification,
)
from app.services.qualification import QualificationService

router = APIRouter(prefix="/api", tags=["qualification"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/targets/{target_id}/qualify",
    response_model=QualifyResult,
    status_code=status.HTTP_201_CREATED,
)
def qualify_target(
    target_id: int, response: Response, session: DbSession, data: QualifyRequest | None = None
) -> QualifyResult:
    """Qualify a target. Idempotent: unchanged inputs return the existing qualification (200)."""
    outcome = QualificationService(session).qualify(target_id, data.profile_id if data else None)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return QualifyResult(
        qualification=read_qualification(outcome.qualification, stale=False),
        created=outcome.created,
    )


@router.get("/targets/{target_id}/qualification", response_model=QualificationRead)
def get_qualification(
    target_id: int, session: DbSession, profile_id: Annotated[int | None, Query()] = None
) -> QualificationRead:
    """The latest qualification for a profile (default: the active one), with `stale`."""
    current = QualificationService(session).current(target_id, profile_id)
    return read_qualification(current.qualification, stale=current.stale)


@router.post("/qualifications/run", response_model=RunReport)
def run_qualifications(session: DbSession, data: RunRequest | None = None) -> RunReport:
    """Qualify every non-dismissed target that changed since its last qualification."""
    return QualificationService(session).run(data.profile_id if data else None, actor="api")
