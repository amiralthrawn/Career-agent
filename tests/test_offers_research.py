"""`Company.offers_research`: same conventions as `contact_research`."""

import pytest
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableError
from app.models import Company
from app.models.enums import OffersResearchStatus
from app.services.offers_research import record_offers_research
from tests import targets_factory as f

STARTED, FOUND, NOT_FOUND = (
    OffersResearchStatus.NOT_STARTED,
    OffersResearchStatus.FOUND,
    OffersResearchStatus.NOT_FOUND,
)


@pytest.fixture
def company(db_session: Session) -> Company:
    return f.company(db_session)


def test_a_company_starts_as_not_searched(company: Company) -> None:
    assert company.offers_research is STARTED and company.offers_research_at is None


def test_a_recorded_offer_makes_the_company_found(company: Company) -> None:
    record_offers_research(company, offers_found=1, completed=False)

    assert company.offers_research is FOUND and company.offers_research_at is not None


def test_a_completed_search_with_named_sources_that_found_nothing_is_not_found(
    company: Company,
) -> None:
    record_offers_research(company, offers_found=0, completed=True, sources_consulted=("Board",))

    assert company.offers_research is NOT_FOUND and company.offers_research_at is not None


def test_not_found_needs_the_sources_that_were_consulted(company: Company) -> None:
    with pytest.raises(UnprocessableError):
        record_offers_research(company, offers_found=0, completed=True)

    assert company.offers_research is STARTED


@pytest.mark.parametrize("sources", [(), ("Board",)])
def test_a_failed_or_incomplete_search_concludes_nothing(
    company: Company, sources: tuple[str, ...]
) -> None:
    record_offers_research(company, offers_found=0, completed=False, sources_consulted=sources)

    assert company.offers_research is STARTED and company.offers_research_at is None


def test_found_is_never_downgraded_by_a_later_empty_search(company: Company) -> None:
    record_offers_research(company, offers_found=1, completed=True, sources_consulted=("Board",))

    record_offers_research(company, offers_found=0, completed=True, sources_consulted=("Board",))

    assert company.offers_research is FOUND


def test_a_later_offer_upgrades_not_found_to_found(company: Company) -> None:
    record_offers_research(company, offers_found=0, completed=True, sources_consulted=("Board",))

    record_offers_research(company, offers_found=2, completed=True, sources_consulted=("Board",))

    assert company.offers_research is FOUND
