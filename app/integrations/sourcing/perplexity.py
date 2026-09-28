"""Perplexity adapter for sourcing (step 3c): a real `WebSearchProvider`, NOT wired by default.

Reuses the EXACT SAME capability gate as company/contact research
(`app.services.sourcing.default_web_search_provider`: `RESEARCH_ENABLED`, `PERPLEXITY_PRESET`, the
`perplexity_api_key` secret) - it is the same account, used for a different task. Nothing new to
configure.

**A raw web search result never states a company's or an offer's identity by itself** (see
`app.services.hit_extraction`): this adapter never derives one from a hit's own title or URL. It
DOES ask the model, as part of its own answer, to explicitly point out - for a result it can
specifically justify from that one page - the literal company name and/or offer title as written
there, using a small, strictly-parsed, line-based convention:

    COMPANY: <result URL> => <company name exactly as written on that page>
    OFFER: <result URL> => <offer title exactly as written on that page>

Parsing is deterministic and defensive: a line that does not match this exact shape, or whose URL
does not match one of THIS call's own search results, is simply ignored - never an error, never a
guess by this adapter. A hit that ends up with no `company_name` attribute is later REJECTED by
`HitExtractor`, exactly like it would reject any other unattributed hit. This is Career-agent
trusting an EXPLICIT provider statement (the same epistemic status as a job board's own structured
field), never an inference this adapter or `HitExtractor` performs on its own.

Limits observed in practice (see `scripts/manual/perplexity_sourcing_smoke_test.py`): most generic
web search results do not carry an unambiguous, single stated company name the model is willing to
point to, so many hits are correctly rejected rather than silently guessed - this adapter is
intentionally conservative, not a high-recall scraper.

Uses `http.client` (standard library), the same transport shape as the research/OpenRouter
adapters (`app.integrations.http_transport`). Verified once manually against the real API; never
called for real in the automated test suite (the transport is injected).
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPSConnection
from typing import Any

from app.core.normalize import normalize_domain, normalize_url
from app.core.secrets import PERPLEXITY_API_KEY, SecretStore, SecretStoreError
from app.integrations.http_transport import HTTPConnection
from app.integrations.provider_context import ProviderContract, render_contract
from app.integrations.sourcing.ports import (
    ProviderError,
    ProviderErrorCode,
    SearchHit,
    SearchQuery,
    WebSearchResult,
)
from app.models.enums import CriterionDimension, SourcingMode

API_HOST = "api.perplexity.ai"
API_PATH = "/v1/agent"
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_LABEL_CHARS = 200

SOURCING_CONTRACT = ProviderContract(
    name="perplexity.sourcing",
    purpose=(
        "Find, via web search, leads for companies and published job offers matching Career-"
        "agent's own search criteria."
    ),
    responsibilities=(
        "run the requested web searches",
        "return raw, sourced search results",
        "when a result's own page explicitly and unambiguously states a company name and/or an "
        "offer title, report it using the exact COMPANY:/OFFER: line format given in the task",
        "never report a company name or offer title you are not confident the page itself states",
    ),
    boundaries=(
        "do not decide whether a company or an offer is a good match",
        "do not qualify, score or rank results",
        "do not claim a company is hiring unless a specific offer page says so",
        "do not invent a company name, offer title, location or contract type",
        "do not reference a URL that is not one of your own search results",
        "do not access or reference any candidate data",
    ),
    upstream_context=(
        "the search mode (published offers, companies for spontaneous applications, or both)",
        "the search profile's own criteria (roles/keywords, locations, sectors, companies, "
        "countries, remote preference, contract type)",
    ),
    downstream_role=(
        "Career-agent's own deterministic extractor decides, from what you reported, whether a "
        "company or an offer actually exists in its records - your COMPANY:/OFFER: lines are the "
        "ONLY way a result can be attributed a name; anything else is discarded, never guessed",
    ),
)

_COMPANY_LINE = re.compile(r"^COMPANY:\s*(\S+)\s*=>\s*(.+)$", re.MULTILINE)
_OFFER_LINE = re.compile(r"^OFFER:\s*(\S+)\s*=>\s*(.+)$", re.MULTILINE)


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(API_HOST, timeout=timeout)


def _terms(query: SearchQuery, dimension: CriterionDimension) -> tuple[str, ...]:
    return query.values_for(dimension)


def _task_input(query: SearchQuery) -> str:
    """TASK CONTEXT: built fresh per call from the profile's own criteria, never candidate data."""
    lines: list[str] = []
    if query.mode is SourcingMode.OFFERS:
        lines.append("Find recently PUBLISHED job offers matching the criteria below.")
    elif query.mode is SourcingMode.COMPANIES:
        lines.append(
            "Find companies that could be a good target for a SPONTANEOUS application (no "
            "specific offer needed) matching the criteria below."
        )
    else:
        lines.append(
            "Find BOTH recently PUBLISHED job offers AND companies that could be a good target "
            "for a SPONTANEOUS application (no specific offer needed), matching the criteria "
            "below. Report each as whichever it actually is - do not force a company into "
            "looking like a specific offer, or the reverse."
        )
    fields = (
        (
            "Roles/keywords",
            (*_terms(query, CriterionDimension.ROLE), *_terms(query, CriterionDimension.KEYWORD)),
        ),
        ("Locations", _terms(query, CriterionDimension.LOCATION)),
        ("Sectors", _terms(query, CriterionDimension.SECTOR)),
        ("Companies of particular interest", _terms(query, CriterionDimension.COMPANY)),
        ("Countries", _terms(query, CriterionDimension.COUNTRY)),
        ("Remote mode", _terms(query, CriterionDimension.REMOTE_MODE)),
    )
    for label, values in fields:
        if values:
            lines.append(f"{label}: " + ", ".join(dict.fromkeys(values)) + ".")
    excluded_companies = query.excluded_for(CriterionDimension.COMPANY)
    if excluded_companies:
        lines.append("Do not include: " + ", ".join(excluded_companies) + ".")
    if query.mode is not SourcingMode.COMPANIES and query.contract_type:
        lines.append(f"Contract type: {query.contract_type.value}.")
    lines.append(
        "For EACH search result where the page itself explicitly states a company name and/or "
        "an offer title, append one line after your answer, in this EXACT format (nothing else "
        "on that line): `COMPANY: <result URL> => <company name as written>` and/or "
        "`OFFER: <result URL> => <offer title as written>`. Skip a result entirely if you cannot "
        "point to where its own page states it - never guess from the URL or a site name."
    )
    return "\n".join(lines)


class PerplexityWebSearchProvider:
    def __init__(
        self,
        secrets: SecretStore,
        preset: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Callable[[], HTTPConnection] | None = None,
    ) -> None:
        self._secrets = secrets
        self._preset = preset
        self._connect = connect or _default_connect(timeout)

    def search(self, query: SearchQuery) -> WebSearchResult:
        api_key = self._resolve_key()
        content = self._call(api_key, query)
        return _parse(content, query.max_results)

    def _resolve_key(self) -> str:
        try:
            api_key = self._secrets.get(PERPLEXITY_API_KEY)
        except SecretStoreError:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE) from None
        if not api_key:
            raise ProviderError(ProviderErrorCode.UNAUTHORIZED)
        return api_key

    def _call(self, api_key: str, query: SearchQuery) -> bytes:
        payload = json.dumps(
            {
                "input": _task_input(query),
                "instructions": render_contract(SOURCING_CONTRACT),
                "preset": self._preset,
                "store": False,  # no server-side retention beyond what the call itself needs
                "tools": [{"type": "web_search", "max_results": query.max_results}],
            }
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        connection = self._connect()
        try:
            connection.request("POST", API_PATH, body=payload, headers=headers)
            response = connection.getresponse()
            content = response.read()
        except TimeoutError:
            raise ProviderError(ProviderErrorCode.TIMEOUT) from None
        except OSError:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()

        _raise_for_status(response.status)
        return content


def _raise_for_status(status: int) -> None:
    if status < 400:
        return
    if status in (401, 403):
        raise ProviderError(ProviderErrorCode.UNAUTHORIZED)
    if status == 429:
        raise ProviderError(ProviderErrorCode.RATE_LIMITED)
    if status >= 500:
        raise ProviderError(ProviderErrorCode.UNAVAILABLE)
    raise ProviderError(ProviderErrorCode.OTHER)


@dataclass(frozen=True)
class _RawResult:
    url: str  # guaranteed non-empty by `_extract_search_results`
    title: str
    snippet: str | None
    published: str | None


def _extract_search_results(output: list[Any]) -> list[_RawResult]:
    """Only the search tool's own `search_results` output item: raw, sourced, never the model's
    free narrative (see `app.integrations.research.perplexity._extract_observations` for the same
    principle applied to company research)."""
    results: list[_RawResult] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "search_results":
            continue
        for entry in item.get("results") or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                continue
            title = entry.get("title")
            snippet = entry.get("snippet")
            published = entry.get("date")
            results.append(
                _RawResult(
                    url=url,
                    title=title if isinstance(title, str) and title else url,
                    snippet=snippet if isinstance(snippet, str) else None,
                    published=published if isinstance(published, str) else None,
                )
            )
    return results


def _extract_narrative(output: list[Any]) -> str:
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def _stated_identity(narrative: str, known_urls: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """`(company by normalised URL, offer title by normalised URL)` - ONLY for a URL that matches
    one of THIS call's own results; anything else (a hallucinated or malformed reference) is
    silently dropped, never raised as an error and never guessed."""
    companies: dict[str, str] = {}
    offers: dict[str, str] = {}
    for url, name in _COMPANY_LINE.findall(narrative):
        normalised = normalize_url(url)
        if normalised is not None and normalised in known_urls and name.strip():
            companies[normalised] = name.strip()[:MAX_LABEL_CHARS]
    for url, title in _OFFER_LINE.findall(narrative):
        normalised = normalize_url(url)
        if normalised is not None and normalised in known_urls and title.strip():
            offers[normalised] = title.strip()[:MAX_LABEL_CHARS]
    return companies, offers


def _parse(body: bytes, max_results: int) -> WebSearchResult:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise ProviderError(ProviderErrorCode.INVALID_RESPONSE) from None
    if not isinstance(data, dict):
        raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)
    if data.get("status") == "failed":
        # Perplexity's run-level error `code` is a free-form string, not a numeric HTTP-like
        # code: no attempt is made to guess a finer mapping than `other` (see the research adapter).
        raise ProviderError(ProviderErrorCode.OTHER)

    output = data.get("output")
    if not isinstance(output, list):
        raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)

    results = _extract_search_results(output)
    known_urls = {normalized for r in results if (normalized := normalize_url(r.url)) is not None}
    companies, offers = _stated_identity(_extract_narrative(output), known_urls)

    hits = tuple(
        SearchHit(
            provider="perplexity",
            title=entry.title,
            url=entry.url,
            snippet=entry.snippet,
            published=entry.published,
            attributes=_attributes(entry.url, companies, offers),
        )
        for entry in results[:max_results]
    )
    return WebSearchResult(completed=True, hits=hits, sources_consulted=_domains(results))


def _attributes(url: str, companies: dict[str, str], offers: dict[str, str]) -> dict[str, str]:
    normalised = normalize_url(url)
    if normalised is None:
        return {}
    attrs: dict[str, str] = {}
    if name := companies.get(normalised):
        attrs["company_name"] = name
    if title := offers.get(normalised):
        attrs["offer_title"] = title
    return attrs


def _domains(results: list[_RawResult]) -> tuple[str, ...]:
    domains: list[str] = []
    for entry in results:
        domain = normalize_domain(entry.url)
        if domain and domain not in domains:
            domains.append(domain)
    return tuple(domains)
