"""Targets: the unit of the application pipeline (with an offer, or spontaneous).

`create_target` is the single code path used by the API and by the CSV import. It is
idempotent: an existing company, offer, contact or target is matched, never duplicated and
never modified, and the outcome says which parts were created.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.core.normalize import normalize_text
from app.models import Company, Contact, Opportunity, Target, TargetContact
from app.models.enums import EmploymentType, TargetStatus
from app.models.sources import SourceSpec
from app.repositories import candidate_brain as brain_repo
from app.repositories import targets as repo
from app.schemas.targets import (
    AttachContact,
    OpportunityInput,
    TargetCreate,
    TargetUpdate,
)
from app.services.companies import CompanyService, Resolution
from app.services.contacts import ContactResolution, ContactService, spec_from_contact_input
from app.services.sources import Provenance, spec_from_input

MAX_PAGE = 500


@dataclass
class TargetOutcome:
    target: Target
    created: bool
    company: Resolution[Company]
    opportunity: Resolution[Opportunity] | None
    contacts: list[ContactResolution] = field(default_factory=list)


def _opportunity_differences(existing: Opportunity, data: OpportunityInput) -> list[str]:
    checks: list[tuple[str, object, object, object, object]] = [
        (
            "title",
            data.title,
            existing.title,
            normalize_text(data.title),
            normalize_text(existing.title),
        ),
        (
            "location",
            data.location,
            existing.location,
            normalize_text(data.location or ""),
            normalize_text(existing.location or ""),
        ),
        (
            "contract_type",
            data.contract_type,
            existing.contract_type,
            data.contract_type,
            existing.contract_type,
        ),
        ("posted_on", data.posted_on, existing.posted_on, data.posted_on, existing.posted_on),
    ]
    result: list[str] = []
    for name, given, stored, given_key, stored_key in checks:
        if not given:
            continue
        if not stored:
            result.append(f"{name}: not_stored")
        elif given_key != stored_key:
            result.append(f"{name}: differs")
    return result


class TargetService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._provenance = Provenance(session)
        self._companies = CompanyService(session, self._provenance)
        self._contacts = ContactService(session, self._provenance)

    # --- creation ---------------------------------------------------------------------

    def create_target(
        self, data: TargetCreate, *, source: SourceSpec | None = None, commit: bool = True
    ) -> TargetOutcome:
        """Create (or match) company, offer, contacts and target in one unit of work.

        `source` is used by the importer; the API passes `data.source` (default: manual entry).
        """
        candidate_id = self._candidate_id()
        spec = source or spec_from_input(data.source)

        if data.company_id is not None:
            company_resolution: Resolution[Company] = Resolution(
                self._companies.get(data.company_id), False
            )
        else:
            assert data.company is not None
            company_resolution = self._companies.find_or_create(data.company, spec)
        company = company_resolution.entity

        opportunity_resolution = (
            self._resolve_opportunity(company, data.opportunity, spec) if data.opportunity else None
        )
        opportunity = opportunity_resolution.entity if opportunity_resolution else None

        target = repo.find_target(
            self._session, candidate_id, company.id, opportunity.id if opportunity else None
        )
        created = target is None
        if target is None:
            target = Target(
                candidate_id=candidate_id,
                company_id=company.id,
                opportunity_id=opportunity.id if opportunity else None,
                contract_type=data.contract_type
                or (opportunity.contract_type if opportunity else None),
                status=TargetStatus.NEW,
                relevance_note=data.relevance_note,
                source_id=self._provenance.source(spec).id,
            )
            repo.stage(self._session, target)

        resolutions: list[ContactResolution] = []
        for contact_input in data.contacts:
            resolution = self._contacts.record_contact(
                company, spec_from_contact_input(contact_input, spec)
            )
            self._link(target, resolution.contact, is_primary=False)
            resolutions.append(resolution)

        self._session.flush()
        self._session.expire(target)
        if commit:
            self._session.commit()
        return TargetOutcome(
            target, created, company_resolution, opportunity_resolution, resolutions
        )

    def _resolve_opportunity(
        self, company: Company, data: OpportunityInput, spec: SourceSpec
    ) -> Resolution[Opportunity]:
        title_key = f"{normalize_text(data.title)}|{normalize_text(data.location or '')}"
        existing: Opportunity | None = None
        if data.url:
            existing = repo.opportunity_by_url(self._session, company.id, data.url)
        if existing is None and data.external_id:
            existing = repo.opportunity_by_external_id(self._session, company.id, data.external_id)
        if existing is None and not data.url and not data.external_id:
            existing = repo.opportunity_by_title_key(self._session, company.id, title_key)
        if existing is not None:
            return Resolution(existing, False, _opportunity_differences(existing, data))

        opportunity = Opportunity(
            company_id=company.id,
            title=data.title,
            title_key=title_key,
            url=data.url,
            external_id=data.external_id,
            contract_type=data.contract_type,
            location=data.location,
            posted_on=data.posted_on,
            description_text=data.description_text,
            status=data.status,
            source_id=self._provenance.source(spec).id,
        )
        repo.stage(self._session, opportunity)
        return Resolution(opportunity, True)

    def _link(self, target: Target, contact: Contact, *, is_primary: bool) -> TargetContact:
        """Link a contact to a target. The first contact of a target becomes its primary one."""
        link = repo.get_link(self._session, target.id, contact.id)
        has_primary = repo.target_has_primary(self._session, target.id)
        if link is None:
            link = TargetContact(
                target_id=target.id,
                contact_id=contact.id,
                company_id=target.company_id,
                is_primary=is_primary or not has_primary,
            )
            if link.is_primary and has_primary:
                self._demote_primary(target.id)
            repo.stage(self._session, link)
        elif is_primary and not link.is_primary:
            self._demote_primary(target.id)
            link.is_primary = True
            self._session.flush()
        return link

    def _demote_primary(self, target_id: int) -> None:
        for existing in self._session.scalars(
            select(TargetContact).where(
                TargetContact.target_id == target_id, TargetContact.is_primary
            )
        ):
            existing.is_primary = False
        self._session.flush()

    # --- reads and updates ------------------------------------------------------------

    def get_target(self, target_id: int) -> Target:
        target = repo.get_target(self._session, self._candidate_id(), target_id)
        if target is None:
            raise NotFoundError(f"Target {target_id} not found")
        return target

    def list_targets(
        self,
        *,
        mode: str | None = None,
        contract_type: EmploymentType | None = None,
        status: TargetStatus | None = None,
        company_id: int | None = None,
        has_email: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Target]:
        return repo.list_targets(
            self._session,
            self._candidate_id(),
            mode=mode,
            contract_type=contract_type,
            status=status,
            company_id=company_id,
            has_email=has_email,
            limit=min(limit, MAX_PAGE),
            offset=offset,
        )

    def update_target(self, target_id: int, data: TargetUpdate) -> Target:
        target = self.get_target(target_id)
        if "status" in data.model_fields_set and data.status is not None:
            target.status = data.status
            if data.status is not TargetStatus.DISMISSED:
                target.dismissed_reason = None
        if "dismissed_reason" in data.model_fields_set and data.dismissed_reason is not None:
            if target.status is not TargetStatus.DISMISSED:
                raise UnprocessableError("A reason can only be given for a dismissed target")
            target.dismissed_reason = data.dismissed_reason or None
        if "relevance_note" in data.model_fields_set:
            target.relevance_note = data.relevance_note
        self._session.commit()
        self._session.refresh(target)
        return target

    def attach_contact(self, target_id: int, data: AttachContact) -> Target:
        target = self.get_target(target_id)
        company = self._companies.get(target.company_id)
        if data.contact_id is not None:
            contact = self._contacts.get(data.contact_id)
            if contact.company_id != target.company_id:
                raise UnprocessableError("This contact belongs to another company")
        else:
            assert data.contact is not None
            spec = spec_from_input(data.contact.source)
            contact = self._contacts.record_contact(
                company, spec_from_contact_input(data.contact, spec)
            ).contact
        self._link(target, contact, is_primary=data.is_primary)
        self._session.flush()
        self._session.expire(target)
        self._session.commit()
        return target

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id
