"""PersonalizationBrief route (step 3b): the only input a future writer may receive."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.personalization import PersonalizationBrief
from app.services.personalization import PersonalizationService

router = APIRouter(prefix="/api/targets", tags=["personalization"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{target_id}/personalization-brief", response_model=PersonalizationBrief)
def get_personalization_brief(
    target_id: int, session: DbSession, profile_id: Annotated[int | None, Query()] = None
) -> PersonalizationBrief:
    """Computed on demand from the latest qualification; nothing is stored, nothing is sent.

    An out-of-date qualification is NOT refused: the brief comes back with
    `qualification.stale = true` and callers that generate content must check it first.
    """
    return PersonalizationService(session).brief(target_id, profile_id)
