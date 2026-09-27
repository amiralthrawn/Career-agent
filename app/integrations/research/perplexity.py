"""Perplexity adapter, behind `ResearchProvider`. NOT wired by default.

Only builds a `PerplexityClient` when the operator has explicitly set `RESEARCH_ENABLED=true`,
given `PERPLEXITY_PRESET` and stored the `perplexity_api_key` secret. Until then, nothing in this
file is ever instantiated, and no test in this repository's automated suite (`pytest`) ever
performs a real network call: the transport is injected (`connect`), so unit tests exercise this
class's error handling with a fake connection, never a socket. A separate, manual script
(`scripts/manual/perplexity_smoke_test.py`) exists for a human to run a real call on demand, with
a public, non-sensitive company and no candidate data.

The API key is read from the existing `SecretStore` (`app.core.secrets`, name
`PERPLEXITY_API_KEY`) at call time, never at construction, and never logged, never included in an
exception, never persisted anywhere by this module. No new secret mechanism is introduced.

**Endpoint, determined by reading Perplexity's current documentation during this step (2026-09):**
the previous "Sonar Chat Completions" endpoint (`/chat/completions`, model + messages array) was
retired on 2026-09-27 in favour of the Agent API (`POST /v1/agent`, `input` + `instructions` +
`preset`/`model`, an `output` array of typed items, citations as a `search_results` output item).
This adapter targets the Agent API. Re-check Perplexity's documentation before relying on this
adapter far in the future: provider APIs change, and this session verified the shape once, on one
date, against public documentation - not against a long-running integration.

Uses `http.client` (standard library), like the OpenRouter adapter: no new HTTP dependency, and
the small transport shape (`app.integrations.http_transport`) is shared between the two.
"""

import json
import time
from collections.abc import Callable
from http.client import HTTPSConnection
from typing import Any

from app.core.secrets import PERPLEXITY_API_KEY, SecretStore, SecretStoreError
from app.integrations.http_transport import HTTPConnection
from app.integrations.provider_context import ProviderContract, TokenUsage, render_contract
from app.integrations.research.ports import (
    Observation,
    ResearchError,
    ResearchErrorCode,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
)

API_HOST = "api.perplexity.ai"
API_PATH = "/v1/agent"
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_EXCERPT_CHARS = 500
MAX_CLAIM_CHARS = 300

# AI Provider Context / Contract (step 6): Perplexity's fixed position in Career-agent for this
# ONE use case. `instructions` (below) carries this; `input` (built per call) carries the task.
COMPANY_RESEARCH_CONTRACT = ProviderContract(
    name="perplexity.company_research",
    purpose=(
        "Provide external research that enriches Career-agent's understanding of companies and "
        "opportunities."
    ),
    responsibilities=(
        "find publicly available information",
        "identify relevant sources",
        "preserve provenance",
        "return observations/facts with source information",
        "identify uncertainty or conflicting information when relevant",
    ),
    boundaries=(
        "do not qualify targets",
        "do not decide whether a company is suitable",
        "do not decide which candidate skills should be claimed",
        "do not generate the final application",
        "do not modify Career-agent's database",
        "do not send emails",
        "do not invent candidate information",
    ),
    upstream_context=(
        "research objective",
        "target company (name, website, location, sector when known)",
        "focus areas relevant to this research",
    ),
    downstream_role=(
        "Career-agent may use the returned research as external observations/evidence for "
        "subsequent, separate business logic - it is never applied as a decision by itself",
    ),
)


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(API_HOST, timeout=timeout)


def _task_input(query: ResearchQuery) -> str:
    """TASK CONTEXT layer: built fresh per call, kept compact - never a verbose free prompt."""
    subject = query.subject
    lines = [f'Research the company "{subject.company_name}".']
    known = [
        f"{label}: {value}"
        for label, value in (
            ("website", subject.website),
            ("location", subject.location),
            ("sector", subject.sector),
        )
        if value
    ]
    if known:
        lines.append("Known context: " + "; ".join(known) + ".")
    lines.append(f"Research objective: {query.objective}")
    if query.focus_areas:
        lines.append("Focus areas: " + ", ".join(query.focus_areas) + ".")
    return "\n".join(lines)


class PerplexityClient:
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

    def research(self, query: ResearchQuery) -> ResearchResult:
        api_key = self._resolve_key()
        started = time.monotonic()
        response_bytes = self._call(api_key, query)
        duration_ms = round((time.monotonic() - started) * 1000)
        return _parse(response_bytes, self._preset, query, duration_ms)

    def _resolve_key(self) -> str:
        try:
            api_key = self._secrets.get(PERPLEXITY_API_KEY)
        except SecretStoreError:
            raise ResearchError(ResearchErrorCode.UNAVAILABLE) from None
        if not api_key:
            raise ResearchError(ResearchErrorCode.UNAUTHORIZED)
        return api_key

    def _call(self, api_key: str, query: ResearchQuery) -> bytes:
        payload = json.dumps(
            {
                "input": _task_input(query),
                "instructions": render_contract(COMPANY_RESEARCH_CONTRACT),
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
            raise ResearchError(ResearchErrorCode.TIMEOUT) from None
        except OSError:
            raise ResearchError(ResearchErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()

        _raise_for_status(response.status)
        return content


def _raise_for_status(status: int) -> None:
    if status < 400:
        return
    if status in (401, 403):
        raise ResearchError(ResearchErrorCode.UNAUTHORIZED)
    if status == 429:
        raise ResearchError(ResearchErrorCode.RATE_LIMITED)
    if status >= 500:
        raise ResearchError(ResearchErrorCode.UNAVAILABLE)
    raise ResearchError(ResearchErrorCode.OTHER)


def _parse(
    body: bytes, requested_model: str, query: ResearchQuery, duration_ms: int
) -> ResearchResult:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise ResearchError(ResearchErrorCode.INVALID_RESPONSE) from None
    if not isinstance(data, dict):
        raise ResearchError(ResearchErrorCode.INVALID_RESPONSE)
    if data.get("status") == "failed":
        # Perplexity's run-level error `code` is a free-form string (per its own documentation),
        # not a numeric HTTP-like code: no attempt is made to guess a finer mapping than `other`.
        raise ResearchError(ResearchErrorCode.OTHER)

    output = data.get("output")
    if not isinstance(output, list):
        raise ResearchError(ResearchErrorCode.INVALID_RESPONSE)
    summary = _extract_summary(output)
    observations = _extract_observations(output, query.max_results)
    if not summary and not observations:
        raise ResearchError(ResearchErrorCode.INVALID_RESPONSE)  # nothing exploitable

    raw_usage = data.get("usage")
    usage_raw: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    return ResearchResult(
        provider="perplexity",
        model=str(data.get("model") or requested_model),
        query=query,
        status=ResearchStatus.OK if observations else ResearchStatus.NO_RESULTS,
        observations=tuple(observations),
        summary=summary,
        usage=TokenUsage(usage_raw.get("input_tokens"), usage_raw.get("output_tokens")),
        duration_ms=duration_ms,
    )


def _extract_summary(output: list[Any]) -> str | None:
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts) if parts else None


def _extract_observations(output: list[Any], max_results: int) -> list[Observation]:
    """Built ONLY from the search tool's own `search_results` output item: a source and its text
    always travel together, exactly as the tool surfaced them. The model's free narrative
    (`_extract_summary`) is never decomposed into per-fact claims - that would risk pairing a
    plausible-looking URL with a sentence the model wrote, not one the search tool verified."""
    observations: list[Observation] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "search_results":
            continue
        for entry in item.get("results") or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                continue  # provenance is mandatory: no URL, no observation
            title = entry.get("title") if isinstance(entry.get("title"), str) else None
            snippet = entry.get("snippet") if isinstance(entry.get("snippet"), str) else None
            published = entry.get("date") if isinstance(entry.get("date"), str) else None
            observations.append(
                Observation(
                    claim=(snippet or title or url)[:MAX_CLAIM_CHARS],
                    source_url=url,
                    source_title=title,
                    excerpt=snippet[:MAX_EXCERPT_CHARS] if snippet else None,
                    published=published,
                )
            )
    return observations[:max_results]
