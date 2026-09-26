"""CV ingestion routes: thin HTTP layer, all rules live in `CVIngestionService`.

Ingesting only creates PROPOSALS. A proposal becomes a Candidate Brain fact only through
`POST /proposals/{id}/accept`, a deliberate human action.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import DocumentIngestion, IngestionProposal
from app.models.enums import EvidenceTargetType, ProposalStatus
from app.schemas.ingestion import (
    CVIngestionRequest,
    IngestionRead,
    IngestionSummaryRead,
    ProposalAccept,
    ProposalRead,
    ProposalReject,
)
from app.services.cv_ingestion import CVIngestionService


def get_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CVIngestionService:
    return CVIngestionService(session, settings)


Service = Annotated[CVIngestionService, Depends(get_service)]

router = APIRouter(prefix="/api/candidate", tags=["cv-ingestion"])


@router.post("/ingestions/cv", response_model=IngestionRead, status_code=status.HTTP_201_CREATED)
def ingest_cv(data: CVIngestionRequest, service: Service) -> DocumentIngestion:
    return service.ingest_cv(data)


@router.get("/ingestions", response_model=list[IngestionSummaryRead])
def list_ingestions(service: Service) -> Sequence[DocumentIngestion]:
    return service.list_ingestions()


@router.get("/ingestions/{ingestion_id}", response_model=IngestionRead)
def get_ingestion(ingestion_id: int, service: Service) -> DocumentIngestion:
    return service.get_ingestion(ingestion_id)


@router.get("/proposals", response_model=list[ProposalRead])
def list_proposals(
    service: Service,
    status: ProposalStatus | None = None,
    kind: EvidenceTargetType | None = None,
    ingestion_id: Annotated[int | None, Query()] = None,
) -> Sequence[IngestionProposal]:
    return service.list_proposals(status=status, kind=kind, ingestion_id=ingestion_id)


@router.get("/proposals/{proposal_id}", response_model=ProposalRead)
def get_proposal(proposal_id: int, service: Service) -> IngestionProposal:
    return service.get_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/accept", response_model=ProposalRead)
def accept_proposal(
    proposal_id: int, decision: ProposalAccept, service: Service
) -> IngestionProposal:
    return service.accept(proposal_id, decision)


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalRead)
def reject_proposal(
    proposal_id: int, decision: ProposalReject, service: Service
) -> IngestionProposal:
    return service.reject(proposal_id, decision)
