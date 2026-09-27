"""`Company.offers_research`: has a search for this company's published offers been done?

Same convention as `contact_research`:

- `found`: at least one offer of the company was recorded from a source;
- `not_found`: a COMPLETED search, made for this company on named sources, showed no offer. It is
  what the sources consulted said, never "the company has no offer" or "is not hiring";
- a failed, incomplete or unsupported search concludes NOTHING: the status does not change;
- discovering a company (spontaneous sourcing) does not change it: a company is not "recruiting"
  because it was found.

`not_found` needs a search targeted at the company; the profile-wide runs of `SourcingService`
are not one, so they only ever record `found`.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.errors import UnprocessableError
from app.models import Company
from app.models.enums import OffersResearchStatus


def record_offers_research(
    company: Company,
    *,
    offers_found: int,
    completed: bool,
    sources_consulted: Sequence[str] = (),
) -> OffersResearchStatus:
    if offers_found > 0:
        company.offers_research = OffersResearchStatus.FOUND
    elif completed:
        if not sources_consulted:
            raise UnprocessableError(
                "A completed search must list the sources consulted to conclude that no offer "
                "was found"
            )
        # Never downgrade: an offer already recorded stays "found".
        if company.offers_research is not OffersResearchStatus.FOUND:
            company.offers_research = OffersResearchStatus.NOT_FOUND
    else:
        return company.offers_research  # nothing concluded
    company.offers_research_at = datetime.now(UTC)
    return company.offers_research
