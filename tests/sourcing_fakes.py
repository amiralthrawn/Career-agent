"""Fictional sourcing providers for the tests. No network, no real service, synthetic data only."""

import re
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.api.sourcing import get_providers
from app.integrations.sourcing.ports import (
    OfferFetchResult,
    ProviderError,
    ProviderErrorCode,
    SearchHit,
    SearchQuery,
    SourcedItem,
    SourcingProviders,
    WebSearchResult,
)
from app.models.enums import SourceKind
from app.models.sources import SourceSpec
from app.schemas.targets import CompanyInput, OpportunityInput

WEB = "fake-web"
BOARD = "fake-board"
SENTINEL = "SENTINEL-must-never-be-stored"


class FakeWebProvider:
    def __init__(
        self,
        hits: list[SearchHit] | None = None,
        *,
        completed: bool = True,
        error: ProviderErrorCode | None = None,
        raises: Exception | None = None,
        sources: tuple[str, ...] = ("fake-index",),
        on_call: Callable[[SearchQuery], None] | None = None,
    ) -> None:
        self.hits = hits or []
        self.completed = completed
        self.error = error
        self.raises = raises
        self.sources = sources
        self.on_call = on_call
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> WebSearchResult:
        self.queries.append(query)
        if self.on_call:
            self.on_call(query)
        if self.raises:
            raise self.raises
        if self.error:
            raise ProviderError(self.error)
        return WebSearchResult(self.completed, tuple(self.hits), self.sources)


class FakeOfferSource:
    def __init__(
        self,
        items: list[SourcedItem] | None = None,
        *,
        completed: bool = True,
        error: ProviderErrorCode | None = None,
    ) -> None:
        self.items = items or []
        self.completed = completed
        self.error = error
        self.queries: list[SearchQuery] = []

    def fetch(self, query: SearchQuery) -> OfferFetchResult:
        self.queries.append(query)
        if self.error:
            raise ProviderError(self.error)
        return OfferFetchResult(self.completed, tuple(self.items), ("fake-board-feed",))


# `HitExtractor` itself never parses a title (see app/services/hit_extraction.py): a real adapter
# states `company_name`/`offer_title` as structured attributes. This test-only helper mirrors that
# for the common "<offer> at <company>" fixture title, purely so existing test call sites can keep
# writing a plain title instead of repeating both attributes everywhere; it is intentionally the
# same strict, unambiguous pattern the extractor itself used to apply, so an AMBIGUOUS title (the
# ones `test_an_ambiguous_or_missing_identity_is_rejected_not_guessed` exercises) still yields no
# attribute and is still rejected by the production code, exactly as before.
_SEPARATOR = re.compile(r"\s(?:at|chez)\s", re.IGNORECASE)
_AMBIGUOUS_COMPANY = re.compile(r"[|–—·:/]|\s-\s|\s\(")


def _implied_attributes(title: str) -> dict[str, str]:
    matches = list(_SEPARATOR.finditer(title))
    if len(matches) != 1:
        return {}
    offer, company = title[: matches[0].start()].strip(), title[matches[0].end() :].strip()
    if not offer or not company or _AMBIGUOUS_COMPANY.search(company):
        return {}
    return {"offer_title": offer, "company_name": company}


def hit(
    title: str = "Data Analyst Intern at Fixture Corp",
    url: str = "https://search.example.invalid/offers/1",
    *,
    provider: str = WEB,
    snippet: str | None = "Join the fictional analytics team.",
    **attributes: str,
) -> SearchHit:
    merged = {**_implied_attributes(title), **attributes}
    return SearchHit(provider=provider, title=title, url=url, snippet=snippet, attributes=merged)


def company_hit(name: str = "Fixture Corp", **attributes: str) -> SearchHit:
    """A company found by a search, with no offer (companies mode)."""
    return hit(
        title="Fixture Corp - about us",
        url="https://search.example.invalid/companies/fixture-corp",
        company_name=name,
        **attributes,
    )


def item(
    company: str = "Fixture Corp",
    title: str | None = "Data Analyst Intern",
    *,
    url: str = "https://board.example.invalid/jobs/1",
    source: SourceSpec | None = None,
    website: str | None = "https://fixture-corp.example.invalid",
    description: str | None = None,
    excerpt: str | None = "Structured feed entry.",
) -> SourcedItem:
    return SourcedItem(
        company=CompanyInput(name=company, website_url=website),
        opportunity=(
            OpportunityInput(title=title, url=url, description_text=description) if title else None
        ),
        source=source
        or SourceSpec(SourceKind.OFFICIAL_API, "Fake job board", url=url, reference="feed:1"),
        excerpt=excerpt,
    )


def install(
    client: TestClient,
    web: FakeWebProvider | None = None,
    offers: FakeOfferSource | None = None,
) -> None:
    providers = SourcingProviders(
        web={WEB: web} if web is not None else {},
        offers={BOARD: offers} if offers is not None else {},
    )
    client.app.dependency_overrides[get_providers] = lambda: providers  # type: ignore[attr-defined]


def start(client: TestClient, mode: str = "offers", provider: str = WEB, **body: Any) -> Any:
    return client.post("/api/search-runs", json={"mode": mode, "provider": provider, **body})


def run_of(client: TestClient, run_id: int) -> dict[str, Any]:
    response = client.get(f"/api/search-runs/{run_id}")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result
