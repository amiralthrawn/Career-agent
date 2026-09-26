"""Contacts and their channels: recording with mandatory provenance, and the ContactFinder sink.

Rules enforced here:
- no contact and no channel without a source; a channel needs a *real* origin (see
  `require_channel_spec`);
- nothing is guessed: only values handed to the service are stored;
- an e-mail whose domain differs from the company's domain is stored as `uncertain`;
- a contact marked `do_not_contact` is never silently overridden;
- absence is represented by NO row. `Company.contact_research` distinguishes "not searched" from
  "searched, nothing found in the sources consulted".
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.core.normalize import email_domain, normalize_channel_value, normalize_name
from app.integrations.contacts.ports import (
    CompanyInfo,
    ContactFinder,
    ContactSearchResult,
    FoundChannel,
    FoundContact,
)
from app.models import Company, Contact, ContactChannel
from app.models.enums import ChannelKind, ContactResearchStatus, InfoStatus
from app.models.sources import SourceSpec
from app.repositories import targets as repo
from app.schemas.targets import ContactInput, ContactUpdate
from app.services.sources import Provenance, require_channel_spec, spec_from_input


@dataclass
class ContactResolution:
    contact: Contact
    created: bool
    channels_created: int = 0
    differences: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RecordedSearch:
    contacts: list[ContactResolution]
    research: ContactResearchStatus


def spec_from_contact_input(data: ContactInput, default: SourceSpec) -> FoundContact:
    """Convert a validated API/import contact into the neutral `FoundContact`."""
    return FoundContact(
        source=spec_from_input(data.source) if data.source else default,
        full_name=data.full_name,
        is_generic=data.is_generic,
        role_title=data.role_title,
        role_category=data.role_category,
        status=data.status,
        channels=tuple(
            FoundChannel(
                kind=channel.kind,
                value=channel.value,
                source=spec_from_input(channel.source),
                status=channel.status,
            )
            for channel in data.channels
        ),
    )


def _same_or_subdomain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


class ContactService:
    def __init__(self, session: Session, provenance: Provenance) -> None:
        self._session = session
        self._provenance = provenance

    # --- recording --------------------------------------------------------------------

    def record_contact(self, company: Company, found: FoundContact) -> ContactResolution:
        if not found.full_name and not found.is_generic:
            raise UnprocessableError("A contact needs a name, or must be a shared mailbox")
        name_key = normalize_name(found.full_name) if found.full_name else None
        contact = (
            repo.contact_by_name_key(self._session, company.id, name_key) if name_key else None
        )
        if contact is None:  # a known address identifies the contact too
            for channel in found.channels:
                email = self._email_value(channel)
                if email and (contact := repo.contact_with_email(self._session, company.id, email)):
                    break

        warnings: list[str] = []
        differences: list[str] = []
        created = contact is None
        if contact is None:
            contact = Contact(
                company_id=company.id,
                full_name=found.full_name,
                name_key=name_key,
                is_generic=found.is_generic,
                role_title=found.role_title,
                role_category=found.role_category,
                status=found.status,
                source_id=self._provenance.source(found.source).id,
            )
            repo.stage(self._session, contact)
        else:
            if found.role_title:
                if not contact.role_title:
                    differences.append("role_title: not_stored")
                elif normalize_name(found.role_title) != normalize_name(contact.role_title):
                    differences.append("role_title: differs")
            if contact.do_not_contact:
                warnings.append("contact_marked_do_not_contact")

        channels_created = sum(
            self._add_channel(company, contact, channel, warnings) for channel in found.channels
        )
        self._session.expire(contact, ["channels"])
        return ContactResolution(contact, created, channels_created, differences, warnings)

    @staticmethod
    def _email_value(channel: FoundChannel) -> str | None:
        if channel.kind is not ChannelKind.EMAIL:
            return None
        return normalize_channel_value(channel.kind.value, channel.value)

    def _add_channel(
        self, company: Company, contact: Contact, channel: FoundChannel, warnings: list[str]
    ) -> bool:
        require_channel_spec(channel.source)
        value = normalize_channel_value(channel.kind.value, channel.value)
        if value is None:
            raise UnprocessableError(f"Invalid {channel.kind.value} value")
        if any(
            existing.kind is channel.kind and existing.value == value
            for existing in contact.channels
        ):
            return False

        status = channel.status
        if channel.kind is ChannelKind.EMAIL:
            owner = repo.contact_with_email(self._session, company.id, value)
            if owner is not None and owner.id != contact.id:
                warnings.append("address_belongs_to_another_contact")
                return False
            if company.domain and not _same_or_subdomain(email_domain(value), company.domain):
                status = InfoStatus.UNCERTAIN
                warnings.append("email_domain_differs_from_company")

        repo.stage(
            self._session,
            ContactChannel(
                contact_id=contact.id,
                kind=channel.kind,
                value=value,
                status=status,
                source_id=self._provenance.source(channel.source).id,
            ),
        )
        return True

    # --- ContactFinder sink -----------------------------------------------------------

    def record_search_result(self, company: Company, result: ContactSearchResult) -> RecordedSearch:
        """Store what a `ContactFinder` returned. The same guarantees apply to every adapter."""
        resolutions = [self.record_contact(company, found) for found in result.contacts]
        if resolutions:
            company.contact_research = ContactResearchStatus.FOUND
        elif result.completed:
            if not result.sources_consulted:
                raise UnprocessableError(
                    "A completed search must list the sources consulted to conclude that "
                    "nothing was found"
                )
            # Never downgrade: contacts already known stay "found".
            if company.contact_research is not ContactResearchStatus.FOUND:
                company.contact_research = ContactResearchStatus.NOT_FOUND
        else:
            return RecordedSearch(resolutions, company.contact_research)  # nothing concluded
        company.contact_research_at = datetime.now(UTC)
        self._session.flush()
        return RecordedSearch(resolutions, company.contact_research)

    def research(self, company: Company, finder: ContactFinder) -> RecordedSearch:
        """Run any finder on a company and record its result through the same sink."""
        info = CompanyInfo(
            id=company.id,
            name=company.name,
            domain=company.domain,
            website_url=company.website_url,
            careers_url=company.careers_url,
        )
        return self.record_search_result(company, finder.find(info))

    # --- reads and updates ------------------------------------------------------------

    def get(self, contact_id: int) -> Contact:
        contact = repo.get_contact(self._session, contact_id)
        if contact is None:
            raise NotFoundError(f"Contact {contact_id} not found")
        return contact

    def update(self, contact_id: int, data: ContactUpdate) -> Contact:
        contact = self.get(contact_id)
        if data.status is not None:
            contact.status = data.status
        if data.verified is not None:
            contact.verified = data.verified
        if data.do_not_contact is not None:
            contact.do_not_contact = data.do_not_contact
        self._session.flush()
        return contact
