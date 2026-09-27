"""Sourcing ports: what a data provider may receive and what it may return.

Two DIFFERENT kinds of provider, because they are two different kinds of evidence:

- `WebSearchProvider`: a web search. It returns raw `SearchHit`s (a title, a URL, an excerpt). A hit
  is only a lead: `HitExtractor` decides, deterministically, whether it identifies a company and
  possibly an offer.
- `OfferSource`: a structured source of PUBLISHED offers (an official API, a job board feed). It
  returns already normalised `SourcedItem`s.

A provider never touches the database and never creates a Company, Opportunity, Target or
qualification: `SourcingService` is the only code that stores anything, so every provider gets
the same guarantees (provenance, de-duplication, qualification).

Failure is explicit. A provider that cannot complete its search raises `ProviderError` (or returns
`completed=False`): the service never turns that into an empty, successful search.

No provider exists yet: this step defines the ports, the types and the recording service only.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    EmploymentType,
    SourcingMode,
)
from app.models.sources import SourceSpec
from app.schemas.targets import CompanyInput, OpportunityInput

MAX_RESULTS_LIMIT = 50


@dataclass(frozen=True)
class QueryCriterion:
    """One active criterion of the search profile, as plain values."""

    dimension: CriterionDimension
    operator: CriterionOperator
    values: tuple[str, ...]
    level: CriterionLevel


@dataclass(frozen=True)
class SearchQuery:
    """A structured request. It holds the profile's search terms, never candidate data."""

    mode: SourcingMode
    profile_id: int
    criteria: tuple[QueryCriterion, ...]
    max_results: int
    # The single contract the profile asks for, when it asks for exactly one; else None.
    contract_type: EmploymentType | None = None

    def values_for(self, dimension: CriterionDimension) -> tuple[str, ...]:
        """Inclusive terms of a dimension (`any_of` criteria), in profile order, de-duplicated."""
        found: dict[str, None] = {}
        for criterion in self.criteria:
            if criterion.dimension is dimension and criterion.operator is CriterionOperator.ANY_OF:
                found.update(dict.fromkeys(criterion.values))
        return tuple(found)

    def excluded_for(self, dimension: CriterionDimension) -> tuple[str, ...]:
        found: dict[str, None] = {}
        for criterion in self.criteria:
            if criterion.dimension is dimension and criterion.operator is CriterionOperator.NONE_OF:
                found.update(dict.fromkeys(criterion.values))
        return tuple(found)


@dataclass(frozen=True)
class SearchHit:
    """A raw web search result: only facts read in the result, nothing inferred.

    `attributes` holds structured facts the ADAPTER read in the result page (for example a
    schema.org `hiringOrganization`), under the documented keys of `HitExtractor`. Anything not
    read stays absent.
    """

    provider: str  # identifier of the provider that returned it
    title: str
    url: str
    snippet: str | None = None
    published: str | None = None  # as the provider gave it (not parsed, not used as a fact)
    retrieved_at: datetime | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcedItem:
    """A normalised finding: a company, an optional offer, where it was read, and the excerpt."""

    company: CompanyInput
    opportunity: OpportunityInput | None
    source: SourceSpec
    excerpt: str | None = None  # the relevant source text, as read


@dataclass(frozen=True)
class WebSearchResult:
    # False when the search could not be carried out completely (error, quota...).
    completed: bool
    hits: tuple[SearchHit, ...] = ()
    sources_consulted: tuple[str, ...] = ()


@dataclass(frozen=True)
class OfferFetchResult:
    completed: bool
    items: tuple[SourcedItem, ...] = ()
    sources_consulted: tuple[str, ...] = ()


class ProviderErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"
    OTHER = "other"


class ProviderError(Exception):
    """A provider could not do its job. Only the CODE is kept: never the message or payload."""

    def __init__(self, code: ProviderErrorCode = ProviderErrorCode.OTHER) -> None:
        super().__init__(code.value)
        self.code = code


class WebSearchProvider(Protocol):
    def search(self, query: SearchQuery) -> WebSearchResult: ...


class OfferSource(Protocol):
    def fetch(self, query: SearchQuery) -> OfferFetchResult: ...


@dataclass(frozen=True)
class SourcingProviders:
    """The providers a run may use, by identifier. Empty by default: nothing real is wired."""

    web: Mapping[str, WebSearchProvider] = field(default_factory=dict)
    offers: Mapping[str, OfferSource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clash = set(self.web) & set(self.offers)
        if clash:
            raise ValueError(f"provider identifiers must be unique: {', '.join(sorted(clash))}")


def default_providers() -> SourcingProviders:
    """No real provider is connected in step 3c."""
    return SourcingProviders()
