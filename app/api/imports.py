"""CSV import routes: preview (writes nothing) and apply (idempotent)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.schemas.imports import ImportReport, ImportRequest
from app.services.target_import import TargetImportService

router = APIRouter(prefix="/api/imports", tags=["imports"])


def get_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TargetImportService:
    return TargetImportService(session, settings, actor="api")


Service = Annotated[TargetImportService, Depends(get_service)]


@router.post("/targets/preview", response_model=ImportReport)
def preview_targets(data: ImportRequest, service: Service) -> ImportReport:
    """Report what the import would do, row by row. Nothing is written."""
    return service.preview(data.source_path)


@router.post("/targets", response_model=ImportReport)
def apply_targets(data: ImportRequest, service: Service) -> ImportReport:
    """Apply the import: valid rows are stored, invalid rows are reported."""
    return service.apply(data.source_path, data.expected_sha256)
