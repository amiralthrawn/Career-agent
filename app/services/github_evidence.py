"""GitHub evidence (step 9): deterministic matching between a requirement's label and the
candidate's own public repositories - never a claim of skill, never a score.

**A repository is complementary evidence, never a fact about the candidate.** A match only means
"this repository's own name/description/topics/languages/README genuinely mentions this
requirement's terms" - never "the candidate is skilled at X", never "this proves professional
experience". `GitHubEvidence.is_personal_project` is always `True`: nothing here is ever presented
as professional experience (see `app.services.application_package`, which keeps it in its own,
separate `github_evidence` bucket, never merged into `claims`/`selected_evidence`, which stay
Candidate-Brain-only).

Matching is a plain, transparent word-overlap check (lower-cased, accent- and punctuation-free,
via `app.core.normalize.normalize_text`) - deterministic and inspectable, not a similarity score.
`rank` orders results by how many distinct requirements a repository matches (ties broken by
name), exactly like `PersonalizationBrief.emphasis_candidates` - explainable, not a score.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.core.normalize import normalize_text
from app.core.secrets import SecretStore
from app.integrations.github.client import GitHubClient
from app.integrations.github.ports import GitHubProvider, GitHubRepository

MIN_TERM_LENGTH = 3  # skips tiny, non-distinctive tokens ("a", "of", "in"...)
DEFAULT_LIMIT = 3  # "quelques éléments très pertinents suffisent" - never the whole account


@dataclass(frozen=True)
class GitHubEvidence:
    """One public repository offered as COMPLEMENTARY evidence for specific requirements."""

    rank: int
    repository_name: str
    url: str
    description: str | None
    primary_language: str | None
    matched_requirement_labels: tuple[str, ...]
    matched_terms: tuple[str, ...]
    source_url: str
    is_personal_project: bool = True  # never presented as professional experience


def _searchable_text(repo: GitHubRepository) -> set[str]:
    parts = " ".join(
        (
            repo.name,
            repo.description or "",
            repo.primary_language or "",
            " ".join(repo.languages),
            " ".join(repo.topics),
            repo.readme_excerpt or "",
        )
    )
    return set(normalize_text(parts).split())


def _terms(label: str) -> set[str]:
    return {word for word in normalize_text(label).split() if len(word) >= MIN_TERM_LENGTH}


def match_repositories(
    repositories: Sequence[GitHubRepository],
    requirement_labels: Sequence[str],
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[GitHubEvidence]:
    """Never raises, never calls the network: pure matching over already-fetched repositories."""
    scored: list[tuple[GitHubRepository, tuple[str, ...], tuple[str, ...]]] = []
    for repo in repositories:
        if repo.is_fork:
            continue  # not the candidate's own work
        text_words = _searchable_text(repo)
        matched_labels: list[str] = []
        matched_terms: set[str] = set()
        for label in requirement_labels:
            hit = _terms(label) & text_words
            if hit:
                matched_labels.append(label)
                matched_terms |= hit
        if matched_labels:
            scored.append((repo, tuple(matched_labels), tuple(sorted(matched_terms))))

    scored.sort(key=lambda item: (-len(item[1]), item[0].name))
    return [
        GitHubEvidence(
            rank=position,
            repository_name=repo.name,
            url=repo.url,
            description=repo.description,
            primary_language=repo.primary_language,
            matched_requirement_labels=labels,
            matched_terms=terms,
            source_url=repo.source_url or repo.url,
        )
        for position, (repo, labels, terms) in enumerate(scored[:limit], start=1)
    ]


def default_github_provider(settings: Settings, secrets: SecretStore) -> GitHubProvider | None:
    """`None` by default: GitHub evidence is then simply absent, never blocking (see
    `app.services.application_package`). Mirrors `app.services.company_research
    .default_research_provider`, minus the secret check: a GitHub token is optional (raises the
    unauthenticated rate limit only), never required for public data.
    """
    if not settings.github_enabled or not settings.github_username:
        return None
    return GitHubClient(secrets)
