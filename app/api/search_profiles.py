"""Search profile routes: profiles, criteria (immutable, versioned) and explicit seeding.

All routes sit behind the API token (see `app.api.router`).
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SearchCriterion, SearchProfile
from app.schemas.search import (
    CriterionInput,
    FromPreferencesRequest,
    SearchCriterionRead,
    SearchProfileCreate,
    SearchProfileRead,
)
from app.services.search_profiles import SearchProfileService

router = APIRouter(prefix="/api/search-profiles", tags=["search-profiles"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[SearchProfileRead])
def list_profiles(session: DbSession) -> Sequence[SearchProfile]:
    return SearchProfileService(session).list_profiles()


@router.post("", response_model=SearchProfileRead, status_code=status.HTTP_201_CREATED)
def create_profile(data: SearchProfileCreate, session: DbSession) -> SearchProfile:
    return SearchProfileService(session).create_profile(data)


@router.post(
    "/from-preferences", response_model=SearchProfileRead, status_code=status.HTTP_201_CREATED
)
def create_profile_from_preferences(
    data: FromPreferencesRequest, session: DbSession
) -> SearchProfile:
    """Explicitly seed a profile from the candidate's preferences and constraints.

    Whatever cannot be evaluated is listed in `unmapped` (codes only), never silently dropped.
    """
    return SearchProfileService(session).from_preferences(data)


@router.post(
    "/{profile_id}/criteria", response_model=SearchProfileRead, status_code=status.HTTP_201_CREATED
)
def add_criterion(profile_id: int, data: CriterionInput, session: DbSession) -> SearchProfile:
    """Add a criterion. With `replaces`, creates a new version and deactivates the old one."""
    return SearchProfileService(session).add_criterion(profile_id, data)


@router.delete("/{profile_id}/criteria/{criterion_id}", response_model=SearchCriterionRead)
def deactivate_criterion(profile_id: int, criterion_id: int, session: DbSession) -> SearchCriterion:
    """Deactivate a criterion. Criteria are never physically deleted or edited."""
    return SearchProfileService(session).deactivate_criterion(profile_id, criterion_id)
