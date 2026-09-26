"""ContactFinder port + the single recording service every future adapter will use."""

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableError
from app.integrations.contacts.ports import (
    CompanyInfo,
    ContactSearchResult,
    FoundChannel,
    FoundContact,
)
from app.models import Company, Contact, ContactChannel
from app.models.enums import ChannelKind, ContactResearchStatus, InfoStatus, SourceKind
from app.models.sources import SourceSpec
from app.services.contacts import ContactService
from app.services.sources import Provenance
from tests import targets_factory as f

TEAM_PAGE = SourceSpec(SourceKind.PUBLIC_PAGE, "Team page", url=f.PAGE)


class StaticFinder:
    """A fake adapter: returns a fixed result and remembers what it was asked."""

    def __init__(self, result: ContactSearchResult) -> None:
        self.result = result
        self.asked: list[CompanyInfo] = []

    def find(self, company: CompanyInfo) -> ContactSearchResult:
        self.asked.append(company)
        return self.result


def pat(*channels: FoundChannel, source: SourceSpec = TEAM_PAGE) -> FoundContact:
    return FoundContact(source=source, full_name="Pat Fixture", role_title="HR", channels=channels)


def email(
    value: str = f.HR_EMAIL, source: SourceSpec = TEAM_PAGE, **kwargs: object
) -> FoundChannel:
    return FoundChannel(kind=ChannelKind.EMAIL, value=value, source=source, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def company(db_session: Session) -> Company:
    return f.company(db_session)


@pytest.fixture
def service(db_session: Session) -> ContactService:
    return ContactService(db_session, Provenance(db_session))


# --- The finder never touches the database ---------------------------------------------


def test_a_finder_receives_plain_data_not_database_objects(
    service: ContactService, company: Company
) -> None:
    finder = StaticFinder(ContactSearchResult(completed=True, sources_consulted=("Team page",)))

    service.research(company, finder)

    (asked,) = finder.asked
    assert isinstance(asked, CompanyInfo) and not isinstance(asked, Company)
    assert (asked.id, asked.domain) == (company.id, f.DOMAIN)


# --- Recording -------------------------------------------------------------------------


def test_found_contacts_are_recorded_with_their_own_sources(
    service: ContactService, company: Company, db_session: Session
) -> None:
    page = SourceSpec(SourceKind.PUBLIC_PAGE, "Contact page", url=f"https://{f.DOMAIN}/contact")
    finder = StaticFinder(
        ContactSearchResult(
            completed=True,
            contacts=(pat(email(source=page)),),
            sources_consulted=("Team page", "Contact page"),
        )
    )

    recorded = service.research(company, finder)

    assert recorded.research is ContactResearchStatus.FOUND
    assert company.contact_research is ContactResearchStatus.FOUND
    assert isinstance(company.contact_research_at, datetime)
    contact = db_session.scalars(select(Contact)).one()
    channel = db_session.scalars(select(ContactChannel)).one()
    assert contact.source.url == f.PAGE and contact.role_title == "HR"
    assert channel.source.url == f"https://{f.DOMAIN}/contact"  # the address has its own source
    assert (channel.value, channel.status, channel.verified) == (
        f.HR_EMAIL,
        InfoStatus.FOUND,
        False,
    )


def test_nothing_is_stored_that_the_finder_did_not_return(
    service: ContactService, company: Company, db_session: Session
) -> None:
    """No guessing: no prenom.nom@domain, no pattern completion, no sibling addresses."""
    finder = StaticFinder(
        ContactSearchResult(completed=True, contacts=(pat(),), sources_consulted=("p",))
    )

    service.research(company, finder)

    assert (
        db_session.scalars(select(ContactChannel)).all() == []
    )  # named contact, domain known: still no email
    with_one = StaticFinder(
        ContactSearchResult(completed=True, contacts=(pat(email()),), sources_consulted=("p",))
    )
    service.research(company, with_one)
    assert [c.value for c in db_session.scalars(select(ContactChannel))] == [f.HR_EMAIL]


def test_recording_the_same_result_twice_creates_nothing_new(
    service: ContactService, company: Company, db_session: Session
) -> None:
    result = ContactSearchResult(completed=True, contacts=(pat(email()),), sources_consulted=("p",))

    first = service.record_search_result(company, result)
    second = service.record_search_result(company, result)

    assert first.contacts[0].created and not second.contacts[0].created
    assert second.contacts[0].channels_created == 0
    assert len(db_session.scalars(select(Contact)).all()) == 1


# --- Found / not found / not searched --------------------------------------------------


def test_a_company_starts_as_not_searched(company: Company) -> None:
    assert company.contact_research is ContactResearchStatus.NOT_STARTED
    assert company.contact_research_at is None


def test_a_completed_empty_search_is_not_found_and_only_that(
    service: ContactService, company: Company
) -> None:
    service.record_search_result(
        company, ContactSearchResult(completed=True, sources_consulted=("Careers page",))
    )

    assert company.contact_research is ContactResearchStatus.NOT_FOUND
    assert company.contact_research_at is not None


def test_a_search_that_could_not_run_concludes_nothing(
    service: ContactService, company: Company
) -> None:
    service.record_search_result(company, ContactSearchResult(completed=False))

    assert company.contact_research is ContactResearchStatus.NOT_STARTED
    assert company.contact_research_at is None


def test_not_found_needs_the_sources_consulted(service: ContactService, company: Company) -> None:
    with pytest.raises(UnprocessableError, match="sources consulted"):
        service.record_search_result(company, ContactSearchResult(completed=True))

    assert company.contact_research is ContactResearchStatus.NOT_STARTED


def test_known_contacts_are_never_downgraded_to_not_found(
    service: ContactService, company: Company
) -> None:
    service.record_search_result(
        company, ContactSearchResult(completed=True, contacts=(pat(),), sources_consulted=("p",))
    )

    service.record_search_result(
        company, ContactSearchResult(completed=True, sources_consulted=("p",))
    )

    assert company.contact_research is ContactResearchStatus.FOUND


def test_a_partial_search_that_found_contacts_is_recorded_as_found(
    service: ContactService, company: Company
) -> None:
    service.record_search_result(company, ContactSearchResult(completed=False, contacts=(pat(),)))

    assert company.contact_research is ContactResearchStatus.FOUND


# --- No source, no address -------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        SourceSpec(SourceKind.IMPORT_FILE, "CSV import", reference="abcd1234:row 1"),
        SourceSpec(SourceKind.MANUAL, "typed by hand"),
    ],
)
def test_a_channel_needs_a_real_origin(
    service: ContactService, company: Company, db_session: Session, source: SourceSpec
) -> None:
    result = ContactSearchResult(
        completed=True, contacts=(pat(email(source=source)),), sources_consulted=("p",)
    )

    with pytest.raises(UnprocessableError, match="needs a source"):
        service.record_search_result(company, result)

    assert db_session.scalars(select(ContactChannel)).all() == []


def test_a_manual_channel_is_accepted_when_it_says_where_it_comes_from(
    service: ContactService, company: Company, db_session: Session
) -> None:
    source = SourceSpec(SourceKind.MANUAL, "Business card", reference="given at an event")

    service.record_search_result(
        company, ContactSearchResult(completed=True, contacts=(pat(email(source=source)),))
    )

    assert db_session.scalars(select(ContactChannel)).one().source.reference == "given at an event"


def test_a_contact_needs_a_name_or_a_shared_mailbox(
    service: ContactService, company: Company
) -> None:
    with pytest.raises(UnprocessableError):
        service.record_contact(company, FoundContact(source=TEAM_PAGE))


@pytest.mark.parametrize("value", ["nope", "a@b", "x@y.invalid, z@y.invalid"])
def test_invalid_addresses_from_a_finder_are_refused(
    service: ContactService, company: Company, value: str
) -> None:
    with pytest.raises(UnprocessableError, match="Invalid email"):
        service.record_contact(company, pat(email(value)))


# --- Uncertain, do-not-contact ---------------------------------------------------------


def test_an_address_on_another_domain_is_recorded_as_uncertain(
    service: ContactService, company: Company, db_session: Session
) -> None:
    resolution = service.record_contact(company, pat(email(f"pat@{f.OTHER_DOMAIN}")))

    assert db_session.scalars(select(ContactChannel)).one().status is InfoStatus.UNCERTAIN
    assert "email_domain_differs_from_company" in resolution.warnings


def test_a_requested_uncertain_status_is_kept(
    service: ContactService, company: Company, db_session: Session
) -> None:
    service.record_contact(company, pat(email(status=InfoStatus.UNCERTAIN)))

    assert db_session.scalars(select(ContactChannel)).one().status is InfoStatus.UNCERTAIN


def test_do_not_contact_is_never_silently_overridden(
    service: ContactService, company: Company, db_session: Session
) -> None:
    service.record_contact(company, pat())
    contact = db_session.scalars(select(Contact)).one()
    contact.do_not_contact = True
    db_session.flush()

    again = service.record_contact(company, pat(email()))

    assert not again.created and "contact_marked_do_not_contact" in again.warnings
    assert db_session.scalars(select(Contact)).one().do_not_contact is True


def test_an_address_owned_by_another_contact_is_not_reassigned(
    service: ContactService, company: Company, db_session: Session
) -> None:
    service.record_contact(company, pat(email()))
    other = FoundContact(source=TEAM_PAGE, full_name="Sam Other")

    service.record_contact(company, other)
    resolution = service.record_contact(
        company, FoundContact(source=TEAM_PAGE, full_name="Sam Other", channels=(email(),))
    )

    assert "address_belongs_to_another_contact" in resolution.warnings
    assert [c.contact_id for c in db_session.scalars(select(ContactChannel))] == [
        db_session.scalars(select(Contact).where(Contact.full_name == "Pat Fixture")).one().id
    ]


def test_any_adapter_gets_the_same_guarantees(
    service: ContactService, company: Company, db_session: Session
) -> None:
    """A different implementation of the port goes through the same sink and the same rules."""

    class OtherAdapter:
        def find(self, company: CompanyInfo) -> ContactSearchResult:
            return ContactSearchResult(
                completed=True,
                contacts=(FoundContact(source=TEAM_PAGE, is_generic=True, channels=(email(),)),),
                sources_consulted=("Other adapter",),
            )

    recorded = service.research(company, OtherAdapter())

    assert recorded.research is ContactResearchStatus.FOUND
    contact = db_session.scalars(select(Contact)).one()
    assert contact.is_generic and contact.full_name is None and contact.source.url == f.PAGE
