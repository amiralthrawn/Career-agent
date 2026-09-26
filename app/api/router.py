from fastapi import APIRouter

from app.api import candidate, health, ingestion

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(candidate.router)
api_router.include_router(ingestion.router)
