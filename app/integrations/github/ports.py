"""GitHubProvider: what Career-agent needs from a public-GitHub research capability (step 9).

**A provider's output is never Career-agent truth**, same principle as `ResearchProvider` (step
6): `GitHubResult` holds PUBLIC, STRUCTURED facts about the candidate's OWN public repositories
(name, description, languages, topics, a README excerpt) - never a claim of skill level, never a
decision about whether a project is "impressive" or "relevant". Deciding whether a repository is
worth mentioning for a given requirement is a separate, later, deterministic step
(`app.services.github_evidence`), never performed by the provider itself.

Not modelled as a `ResearchProvider`/`Observation`: that port is for PROSE research about a
COMPANY, sourced as free-text claims; this is STRUCTURED data about the CANDIDATE's own public
repositories (a fixed, typed shape: name, languages, topics, README), fetched from one fixed
public source (the GitHub API for one username), not a general web search. Forcing one shape onto
both would blur "external research about a company" with "the candidate's own public work".
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class GitHubRepository:
    """One public repository, exactly as GitHub reports it. Never a claim of skill or quality."""

    name: str
    url: str
    description: str | None = None
    primary_language: str | None = None
    # Every language GitHub detected in the repo (not just the primary one), when available.
    languages: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    # A short excerpt of the repository's own README, never rewritten or summarised by an LLM.
    readme_excerpt: str | None = None
    is_fork: bool = False
    # The exact API URL consulted for this repository - provenance, kept even after selection.
    source_url: str = ""


class GitHubStatus(StrEnum):
    OK = "ok"  # at least one public repository
    NO_RESULTS = "no_results"  # the account exists (or was reachable) but has no public repository


@dataclass(frozen=True)
class GitHubResult:
    username: str
    status: GitHubStatus
    repositories: tuple[GitHubRepository, ...] = ()
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class GitHubErrorCode(StrEnum):
    NOT_FOUND = "not_found"  # the username does not exist / has no public profile
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"


class GitHubError(Exception):
    """A call could not be completed. Carries only a CODE, never a message or any payload."""

    def __init__(self, code: GitHubErrorCode = GitHubErrorCode.OTHER) -> None:
        super().__init__(code.value)
        self.code = code


class GitHubProvider(Protocol):
    def research(self, username: str) -> GitHubResult: ...
