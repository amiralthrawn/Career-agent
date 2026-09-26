"""Companies: find-or-create with conservative de-duplication.

An existing company is NEVER modified by a later source: differences are reported instead, so a
value's provenance can never be silently mixed. The record keeps the source it was created from.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.core.normalize import normalize_domain, normalize_name, normalize_text
from app.models import Company
from app.models.sources import SourceSpec
from app.repositories import targets as repo
from app.schemas.targets import CompanyInput
from app.services.sources import Provenance

MAX_PAGE = 500


@dataclass
class Resolution[T]:
    """A found-or-created record, with what differs from the values that were provided."""

    entity: T
    created: bool
    differences: list[str] = field(default_factory=list)


def _differences(existing: Company, data: CompanyInput, domain: str | None) -> list[str]:
    """Fields given by the caller that are missing from (`not_stored`) or differ from the record."""
    pairs: list[tuple[str, str | None, str | None, str | None, str | None]] = [
        ("name", data.name, existing.name, normalize_name(data.name), existing.name_key),
        ("website_url", data.website_url, existing.website_url, domain, existing.domain),
        (
            "careers_url",
            data.careers_url,
            existing.careers_url,
            data.careers_url,
            existing.careers_url,
        ),
        ("siren", data.siren, existing.siren, data.siren, existing.siren),
        (
            "location",
            data.location,
            existing.location,
            normalize_text(data.location or ""),
            normalize_text(existing.location or ""),
        ),
        (
            "country_code",
            data.country_code,
            existing.country_code,
            data.country_code,
            existing.country_code,
        ),
        (
            "sector",
            data.sector,
            existing.sector,
            normalize_text(data.sector or ""),
            normalize_text(existing.sector or ""),
        ),
    ]
    result: list[str] = []
    for name, given, stored, given_key, stored_key in pairs:
        if not given:
            continue
        if not stored:
            result.append(f"{name}: not_stored")
        elif given_key != stored_key:
            result.append(f"{name}: differs")
    return result


class CompanyService:
    def __init__(self, session: Session, provenance: Provenance) -> None:
        self._session = session
        self._provenance = provenance

    def find_or_create(self, data: CompanyInput, spec: SourceSpec) -> Resolution[Company]:
        name_key = normalize_name(data.name)
        if not name_key:
            raise UnprocessableError("The company name has no usable characters")
        domain = normalize_domain(data.website_url) if data.website_url else None

        existing = self._find(data, name_key, domain)
        if existing is not None:
            return Resolution(existing, False, _differences(existing, data, domain))

        company = Company(
            name=data.name,
            name_key=name_key,
            domain=domain,
            website_url=data.website_url,
            careers_url=data.careers_url,
            siren=data.siren,
            location=data.location,
            country_code=data.country_code,
            sector=data.sector,
            source_id=self._provenance.source(spec).id,
        )
        repo.stage(self._session, company)
        return Resolution(company, True)

    def _find(self, data: CompanyInput, name_key: str, domain: str | None) -> Company | None:
        if domain and (found := repo.company_by_domain(self._session, domain)):
            return found
        if data.siren and (found := repo.company_by_siren(self._session, data.siren)):
            return found
        # Same normalised name: only the same company if the locations agree (both given and
        # equal, or both absent) and the domains do not contradict each other. A wrong merge
        # is worse than a duplicate that a human can see.
        wanted_location = normalize_text(data.location or "")
        for candidate in repo.companies_by_name_key(self._session, name_key):
            if normalize_text(candidate.location or "") != wanted_location:
                continue
            if domain and candidate.domain and candidate.domain != domain:
                continue
            return candidate
        return None

    def get(self, company_id: int) -> Company:
        company = repo.get_company(self._session, company_id)
        if company is None:
            raise NotFoundError(f"Company {company_id} not found")
        return company

    def list(self, *, query: str | None, limit: int, offset: int) -> Sequence[Company]:
        key = normalize_text(query) if query else None
        return repo.list_companies(
            self._session, query_key=key, limit=min(limit, MAX_PAGE), offset=offset
        )
