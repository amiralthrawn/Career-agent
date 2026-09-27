from fastapi import APIRouter, Depends

from app.api import (
    applications,
    candidate,
    contact_research,
    drafts,
    health,
    imports,
    ingestion,
    personalization,
    qualification,
    requirements,
    search_profiles,
    send_batches,
    sourcing,
    targets,
)
from app.core.security import require_api_token

api_router = APIRouter()
api_router.include_router(health.router)
# Every /api/* route requires the local API token; /health stays public.
protected = [Depends(require_api_token)]
api_router.include_router(candidate.router, dependencies=protected)
api_router.include_router(ingestion.router, dependencies=protected)
api_router.include_router(targets.router, dependencies=protected)
api_router.include_router(imports.router, dependencies=protected)
api_router.include_router(search_profiles.router, dependencies=protected)
api_router.include_router(qualification.router, dependencies=protected)
api_router.include_router(requirements.router, dependencies=protected)
api_router.include_router(personalization.router, dependencies=protected)
api_router.include_router(sourcing.router, dependencies=protected)
api_router.include_router(drafts.router, dependencies=protected)
api_router.include_router(contact_research.router, dependencies=protected)
api_router.include_router(applications.router, dependencies=protected)
api_router.include_router(send_batches.router, dependencies=protected)
