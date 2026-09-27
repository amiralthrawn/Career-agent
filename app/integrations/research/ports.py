"""ResearchProvider: what Career-agent needs from an external research capability (step 6).

**A provider's output is never Career-agent's truth** (see `docs/providers.md`). `ResearchResult`
holds external, sourced OBSERVATIONS - never a business decision. Concretely, nothing here can
express `good_target = true`, `match = 85` or `apply = true`: there is no field for it, and no
method turns an `Observation` into a `Qualification`, a `Target` status change or a sent e-mail.
Only Career-agent's own, existing decision points may later choose to use a research result as
input - that is a separate, deliberate step, not something this port performs.

Not reused from `app.integrations.sourcing`: that package's `SearchQuery`/`SearchHit` model a job
or company LISTING found by a web search or a job board (title, URL, an offer to normalise into a
`Company`/`Opportunity`); this models free-text RESEARCH about an already-known company (its
activity, tech environment, recent news), with no offer-shaped output at all. Forcing one shape
onto both would make either the offer-sourcing rules leak into research, or the reverse - so this
is a small, separate, analogous abstraction instead of a forced reuse (see `docs/providers.md`).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.integrations.provider_context import TokenUsage

MAX_FOCUS_AREAS = 10
MAX_RESULTS_LIMIT = 20


@dataclass(frozen=True)
class ResearchSubject:
    """The company Career-agent asks about. No candidate data ever belongs here."""

    company_name: str
    website: str | None = None
    location: str | None = None
    sector: str | None = None


@dataclass(frozen=True)
class ResearchQuery:
    """Career-agent's OWN framing of what matters. A provider never chooses the objective."""

    objective: str
    subject: ResearchSubject
    focus_areas: tuple[str, ...] = ()
    max_results: int = 10

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("a research query needs an objective")
        if not self.subject.company_name.strip():
            raise ValueError("a research query needs a company name")
        if len(self.focus_areas) > MAX_FOCUS_AREAS:
            raise ValueError(f"at most {MAX_FOCUS_AREAS} focus areas are allowed")
        if not 1 <= self.max_results <= MAX_RESULTS_LIMIT:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")


@dataclass(frozen=True)
class Observation:
    """One sourced, external claim. An OBSERVATION, never Career-agent-verified evidence."""

    claim: str
    source_url: str | None = None
    source_title: str | None = None
    excerpt: str | None = None
    published: str | None = None  # exactly as the provider gave it: never parsed or completed
    metadata: Mapping[str, str] = field(default_factory=dict)


class ResearchStatus(StrEnum):
    OK = "ok"  # at least one sourced observation
    NO_RESULTS = "no_results"  # the provider completed but found nothing sourced


@dataclass(frozen=True)
class ResearchResult:
    provider: str
    model: str
    query: ResearchQuery
    status: ResearchStatus
    observations: tuple[Observation, ...] = ()
    # The provider's own narrative synthesis, kept SEPARATE from `observations`: it is prose, not
    # a per-fact sourced claim, and must never be read as if each sentence had its own citation.
    summary: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    usage: TokenUsage | None = None
    duration_ms: int | None = None


class ResearchErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"
    OTHER = "other"


class ResearchError(Exception):
    """A call could not be completed. Carries only a CODE: never a message or any payload."""

    def __init__(self, code: ResearchErrorCode = ResearchErrorCode.OTHER) -> None:
        super().__init__(code.value)
        self.code = code


class ResearchProvider(Protocol):
    def research(self, query: ResearchQuery) -> ResearchResult: ...
