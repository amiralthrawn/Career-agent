"""Sourcing routes (step 3c): start a search run and consult it.

A run is synchronous and bounded (`max_results` <= 50). The response always describes the run:
a provider failure is a run with `status: failed` (never an empty, successful search). The
providers come from an injectable registry that is EMPTY by default: no real provider is wired,
so without configuration every run is refused (422) before anything is called.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.sourcing.ports import SourcingProviders, default_providers
from app.schemas.sourcing import SearchRunCreate, SearchRunDetail, SearchRunRead
from app.services.sourcing import SourcingService

router = APIRouter(prefix="/api/search-runs", tags=["sourcing"])

DbSession = Annotated[Session, Depends(get_db)]


def get_providers() -> SourcingProviders:
    return default_providers()


Providers = Annotated[SourcingProviders, Depends(get_providers)]


def _detail(service: SourcingService, run_id: int) -> SearchRunDetail:
    run = service.get(run_id)
    return SearchRunDetail.model_validate(
        {**SearchRunRead.model_validate(run).model_dump(), "items": service.items(run_id)},
        from_attributes=True,
    )


@router.post("", response_model=SearchRunDetail, status_code=status.HTTP_201_CREATED)
def start_search_run(
    data: SearchRunCreate, session: DbSession, providers: Providers
) -> SearchRunDetail:
    """Run a search with the active (or given) profile, ingest and qualify what it found."""
    service = SourcingService(session, providers)
    run = service.run(data, actor="api")
    return _detail(service, run.id)


@router.get("", response_model=list[SearchRunRead])
def list_search_runs(
    session: DbSession,
    providers: Providers,
    profile_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SearchRunRead]:
    runs = SourcingService(session, providers).list(
        profile_id=profile_id, limit=limit, offset=offset
    )
    return [SearchRunRead.model_validate(run) for run in runs]


@router.get("/{run_id}", response_model=SearchRunDetail)
def get_search_run(run_id: int, session: DbSession, providers: Providers) -> SearchRunDetail:
    return _detail(SourcingService(session, providers), run_id)
