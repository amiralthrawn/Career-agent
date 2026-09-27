"""Contact research routes (step 8): search, list, accept, reject. No send route: nothing here
ever contacts anyone, it only proposes sourced observations for a human to review.

The research provider comes from the same injectable, off-by-default dependency as company
research (step 6): `None` unless `RESEARCH_ENABLED=true`, `PERPLEXITY_PRESET` is set and the
`perplexity_api_key` secret is stored - every search is refused (422) otherwise.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.secrets import SecretStoreError, get_secret_store
from app.integrations.research.ports import ResearchProvider
from app.models import ContactResearchObservation
from app.models.enums import ProposalStatus
from app.schemas.contact_research import (
    ContactBatchItemRead,
    ContactBatchReportRead,
    ContactBatchRequest,
    ContactObservationAccept,
    ContactObservationRead,
    ContactObservationReject,
    ContactSearchRequest,
)
from app.services.company_research import default_research_provider
from app.services.contact_research import ContactResearchService
from app.services.contact_research_batch import DEFAULT_ROLE_CATEGORIES, ContactResearchBatchService

router = APIRouter(prefix="/api", tags=["contact-research"])

DbSession = Annotated[Session, Depends(get_db)]


def get_research_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResearchProvider | None:
    """`None` by default: contact research is then refused, explicitly, before anything runs."""
    try:
        store = get_secret_store()
    except SecretStoreError:
        return None  # fail closed: no secure backend, no provider
    return default_research_provider(settings, store)


Provider = Annotated[ResearchProvider | None, Depends(get_research_provider)]


def get_service(session: DbSession, provider: Provider) -> ContactResearchService:
    return ContactResearchService(session, provider)


def get_batch_service(session: DbSession, provider: Provider) -> ContactResearchBatchService:
    return ContactResearchBatchService(session, provider)


Service = Annotated[ContactResearchService, Depends(get_service)]
BatchService = Annotated[ContactResearchBatchService, Depends(get_batch_service)]


@router.post(
    "/targets/{target_id}/contact-research",
    response_model=list[ContactObservationRead],
    status_code=201,
)
def search_contacts(
    target_id: int, data: ContactSearchRequest, service: Service
) -> list[ContactResearchObservation]:
    return service.search(target_id, data.role_categories)


@router.get("/contact-research/observations", response_model=list[ContactObservationRead])
def list_observations(
    service: Service,
    target_id: int | None = None,
    company_id: int | None = None,
    status: ProposalStatus | None = None,
) -> Sequence[ContactResearchObservation]:
    return service.list_observations(target_id=target_id, company_id=company_id, status=status)


@router.get(
    "/contact-research/observations/{observation_id}", response_model=ContactObservationRead
)
def get_observation(observation_id: int, service: Service) -> ContactResearchObservation:
    return service.get_observation(observation_id)


@router.post(
    "/contact-research/observations/{observation_id}/accept",
    response_model=ContactObservationRead,
)
def accept_observation(
    observation_id: int, decision: ContactObservationAccept, service: Service
) -> ContactResearchObservation:
    return service.accept(observation_id, decision)


@router.post(
    "/contact-research/observations/{observation_id}/reject",
    response_model=ContactObservationRead,
)
def reject_observation(
    observation_id: int, decision: ContactObservationReject, service: Service
) -> ContactResearchObservation:
    return service.reject(observation_id, decision)


@router.post("/contact-research/batch", response_model=ContactBatchReportRead)
def run_batch(data: ContactBatchRequest, service: BatchService) -> ContactBatchReportRead:
    report = service.run(
        target_ids=data.target_ids,
        max_targets=data.max_targets,
        role_categories=data.role_categories or DEFAULT_ROLE_CATEGORIES,
        max_research_calls=data.max_research_calls,
    )
    return ContactBatchReportRead(
        targets_processed=report.targets_processed,
        searches_needed=report.searches_needed,
        searches_researched=report.searches_researched,
        searches_failed=report.searches_failed,
        searches_skipped_budget=report.searches_skipped_budget,
        searches_skipped_already_known=report.searches_skipped_already_known,
        observations_created=report.observations_created,
        items=[
            ContactBatchItemRead(
                company_id=item.company_id,
                role_category=item.role_category,
                targets_affected=list(item.targets_affected),
                outcome=item.outcome.value,
                observations_created=item.observations_created,
            )
            for item in report.items
        ],
    )
