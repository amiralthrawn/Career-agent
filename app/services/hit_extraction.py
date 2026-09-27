"""HitExtractor: a deterministic `SearchHit -> SourcedItem` converter. No network, no AI.

Nothing is inferred. A company or an offer exists in the output only if the hit STATES it:

- structured `attributes` the adapter read in the result (documented keys below), or
- in `offers` mode only, one unambiguous title pattern: `<offer title> at <company>` /
  `<offer title> chez <company>`, with exactly one separator and no other separator character
  (`|`, `-`, `:`, `/`...) around the company. Anything else is ambiguous and is not guessed.

Never derived: the company website from the hit URL (a job board is not the company), the offer
description from the snippet, the contract or location from the title, the publication date from
`published`, the fact that a company is hiring.

A hit that does not identify a company (or, in `offers` mode, an offer) is REJECTED with a
structured reason, never silently turned into a target. In `companies` mode no Opportunity is ever
created, even if offer attributes are present: the result only says that the company exists.

Attribute keys (all optional unless stated): company: `company_name` (required in `companies`
mode), `company_website`, `company_careers_url`, `company_location`, `company_country`,
`company_sector`; offer (used in `offers` mode only): `offer_title`, `offer_location`,
`offer_contract` (an `EmploymentType` value), `offer_remote` (a `RemoteMode` value),
`offer_posted` (YYYY, YYYY-MM or YYYY-MM-DD), `offer_external_id`. Unknown keys are ignored.
"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.normalize import normalize_url
from app.integrations.sourcing.ports import SearchHit, SourcedItem
from app.models.enums import (
    EmploymentType,
    ItemReason,
    RemoteMode,
    SourceKind,
    SourcingMode,
)
from app.models.sources import SourceSpec
from app.schemas.targets import CompanyInput, OpportunityInput

MAX_EXCERPT_CHARS = 500

_SEPARATOR = re.compile(r"\s(?:at|chez)\s", re.IGNORECASE)
# Characters that make the "company" part of a title ambiguous (a suffix, a location, a site...).
_AMBIGUOUS_COMPANY = re.compile(r"[|–—·:/]|\s-\s|\s\(")

_COMPANY_ATTRIBUTES = {
    "company_website": "website_url",
    "company_careers_url": "careers_url",
    "company_location": "location",
    "company_country": "country_code",
    "company_sector": "sector",
}
_OFFER_ATTRIBUTES = {
    "offer_location": "location",
    "offer_posted": "posted_on",
    "offer_external_id": "external_id",
}


@dataclass(frozen=True)
class Rejection:
    reason: ItemReason
    fields: tuple[str, ...] = ()  # names of the invalid fields, never their values
    source_url: str | None = None  # public URL of the rejected hit, for review


@dataclass(frozen=True)
class Extraction:
    items: tuple[SourcedItem, ...] = ()
    rejections: tuple[Rejection, ...] = ()


def _clean(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text or None


def _from_title(title: str) -> tuple[str, str] | None:
    """`(offer title, company)` when the title says it unambiguously, else None."""
    matches = list(_SEPARATOR.finditer(title))
    if len(matches) != 1:
        return None
    offer = title[: matches[0].start()].strip()
    company = title[matches[0].end() :].strip()
    if not offer or not company or _AMBIGUOUS_COMPANY.search(company):
        return None
    return offer, company


def _codes(prefix: str, error: ValidationError) -> tuple[str, ...]:
    return tuple(
        sorted(
            {f"{prefix}.{'.'.join(str(part) for part in item['loc'])}" for item in error.errors()}
        )
    )


class HitExtractor:
    def extract(self, hit: SearchHit, mode: SourcingMode) -> Extraction:
        raw_url = _clean(hit.url)
        if raw_url is None:
            return self._reject(ItemReason.MISSING_SOURCE_URL)
        url = normalize_url(raw_url)
        if url is None:
            return self._reject(ItemReason.INVALID_SOURCE_URL)
        provider = _clean(hit.provider)
        if provider is None:
            return self._reject(ItemReason.INVALID_PROVENANCE, source_url=url)

        attributes = {key: _clean(value) for key, value in hit.attributes.items()}
        offer_title: str | None = None
        company_name = attributes.get("company_name")
        if mode is SourcingMode.OFFERS:
            offer_title = attributes.get("offer_title")
            if company_name is None or offer_title is None:
                parsed = _from_title(_clean(hit.title) or "")
                if parsed is not None:
                    offer_title = offer_title or parsed[0]
                    company_name = company_name or parsed[1]
        if company_name is None:
            return self._reject(ItemReason.MISSING_COMPANY_NAME, source_url=url)
        if mode is SourcingMode.OFFERS and offer_title is None:
            return self._reject(ItemReason.MISSING_OFFER_TITLE, source_url=url)

        try:
            company = CompanyInput(
                name=company_name,
                **{
                    field: attributes[key]
                    for key, field in _COMPANY_ATTRIBUTES.items()
                    if attributes.get(key)
                },
            )
        except ValidationError as error:
            return self._reject(ItemReason.INVALID_FIELD, _codes("company", error), url)

        opportunity: OpportunityInput | None = None
        if mode is SourcingMode.OFFERS:
            assert offer_title is not None
            try:
                opportunity = OpportunityInput(
                    title=offer_title,
                    url=url,  # the page the hit points to; it is also the provenance
                    **self._offer_fields(attributes),
                )
            except (ValidationError, ValueError) as error:
                fields = (
                    _codes("opportunity", error)
                    if isinstance(error, ValidationError)
                    else ("opportunity.value",)
                )
                return self._reject(ItemReason.INVALID_FIELD, fields, url)

        source = SourceSpec(
            kind=SourceKind.PUBLIC_PAGE,
            label=f"Web search: {provider}"[:120],
            url=url,
            reference=provider[:100],
        )
        excerpt = _clean(hit.snippet)
        item = SourcedItem(
            company=company,
            opportunity=opportunity,
            source=source,
            excerpt=excerpt[:MAX_EXCERPT_CHARS] if excerpt else None,
        )
        return Extraction(items=(item,))

    @staticmethod
    def _offer_fields(attributes: dict[str, str | None]) -> dict[str, object]:
        fields: dict[str, object] = {
            field: attributes[key]
            for key, field in _OFFER_ATTRIBUTES.items()
            if attributes.get(key)
        }
        # Closed vocabularies: the exact value, or the field is invalid. Nothing is guessed.
        if contract := attributes.get("offer_contract"):
            fields["contract_type"] = EmploymentType(contract)
        if remote := attributes.get("offer_remote"):
            fields["remote_mode"] = RemoteMode(remote)
        return fields

    @staticmethod
    def _reject(
        reason: ItemReason, fields: tuple[str, ...] = (), source_url: str | None = None
    ) -> Extraction:
        return Extraction(rejections=(Rejection(reason, fields, source_url),))
