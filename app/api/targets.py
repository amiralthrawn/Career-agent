"""Target pipeline routes: targets, companies, contacts. Thin HTTP layer over the services.

All routes sit behind the API token (see `app.api.router`).
"""

from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import NotFoundError, UnprocessableError
from app.models import Company, Contact, Target
from app.models.enums import EmploymentType, TargetStatus
from app.schemas.qualification import QualificationFilter
from app.schemas.targets import (
    AttachContact,
    CompanyDetailRead,
    CompanyRead,
    ContactInput,
    ContactRead,
    ContactUpdate,
    TargetCreate,
    TargetCreateResult,
    TargetRead,
    TargetUpdate,
)
from app.services.companies import CompanyService
from app.services.contacts import ContactService, spec_from_contact_input
from app.services.search_profiles import SearchProfileService
from app.services.sources import Provenance, spec_from_input
from app.services.targets import TargetService

router = APIRouter(prefix="/api", tags=["targets"])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/targets", response_model=TargetCreateResult, status_code=status.HTTP_201_CREATED)
def create_target(data: TargetCreate, response: Response, session: DbSession) -> TargetCreateResult:
    """Create a target (company + optional offer + contacts). Idempotent: an existing target
    is matched (200, `created: false`) instead of duplicated."""
    outcome = TargetService(session).create_target(data)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return TargetCreateResult(
        target=TargetRead.model_validate(outcome.target),
        created=outcome.created,
        company_created=outcome.company.created,
        opportunity_created=bool(outcome.opportunity and outcome.opportunity.created),
    )


@router.get("/targets", response_model=list[TargetRead])
def list_targets(
    session: DbSession,
    mode: Literal["offer", "spontaneous"] | None = None,
    contract_type: EmploymentType | None = None,
    target_status: Annotated[TargetStatus | None, Query(alias="status")] = None,
    company_id: int | None = None,
    has_email: bool | None = None,
    qualification: QualificationFilter | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Sequence[Target]:
    """`qualification` filters on the latest qualification for the ACTIVE search profile
    (`none` = not qualified yet)."""
    profile_id: int | None = None
    if qualification is not None:
        try:
            profile_id = SearchProfileService(session).active_profile().id
        except NotFoundError:
            raise UnprocessableError(
                "A qualification filter needs an active search profile"
            ) from None
    return TargetService(session).list_targets(
        mode=mode,
        contract_type=contract_type,
        status=target_status,
        company_id=company_id,
        has_email=has_email,
        limit=limit,
        offset=offset,
        qualification=qualification,
        qualification_profile_id=profile_id,
    )


@router.get("/targets/{target_id}", response_model=TargetRead)
def get_target(target_id: int, session: DbSession) -> Target:
    return TargetService(session).get_target(target_id)


@router.patch("/targets/{target_id}", response_model=TargetRead)
def update_target(target_id: int, data: TargetUpdate, session: DbSession) -> Target:
    return TargetService(session).update_target(target_id, data)


@router.post("/targets/{target_id}/contacts", response_model=TargetRead)
def attach_contact(target_id: int, data: AttachContact, session: DbSession) -> Target:
    return TargetService(session).attach_contact(target_id, data)


@router.get("/companies", response_model=list[CompanyRead])
def list_companies(
    session: DbSession, q: str | None = None, limit: Limit = 100, offset: Offset = 0
) -> Sequence[Company]:
    return CompanyService(session, Provenance(session)).list(query=q, limit=limit, offset=offset)


@router.get("/companies/{company_id}", response_model=CompanyDetailRead)
def get_company(company_id: int, session: DbSession) -> Company:
    return CompanyService(session, Provenance(session)).get(company_id)


@router.get("/companies/{company_id}/contacts", response_model=list[ContactRead])
def list_company_contacts(company_id: int, session: DbSession) -> Sequence[Contact]:
    return CompanyService(session, Provenance(session)).get(company_id).contacts


@router.post(
    "/companies/{company_id}/contacts",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
)
def create_company_contact(
    company_id: int, data: ContactInput, session: DbSession, response: Response
) -> Contact:
    provenance = Provenance(session)
    company = CompanyService(session, provenance).get(company_id)
    resolution = ContactService(session, provenance).record_contact(
        company, spec_from_contact_input(data, spec_from_input(data.source))
    )
    session.commit()
    if not resolution.created:
        response.status_code = status.HTTP_200_OK
    return resolution.contact


@router.patch("/contacts/{contact_id}", response_model=ContactRead)
def update_contact(contact_id: int, data: ContactUpdate, session: DbSession) -> Contact:
    contact = ContactService(session, Provenance(session)).update(contact_id, data)
    session.commit()
    return contact
