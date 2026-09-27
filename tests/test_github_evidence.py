"""Deterministic matching between requirement labels and public repositories (step 9), and the
`default_github_provider` capability gate (mirrors `company_research`'s)."""

from app.core.config import Settings
from app.core.secrets import InMemorySecretStore
from app.integrations.github.ports import GitHubRepository
from app.services.github_evidence import default_github_provider, match_repositories


def repo(**overrides: object) -> GitHubRepository:
    defaults: dict[str, object] = dict(
        name="Commodity-Arbitrage-Analysis",
        url="https://github.com/fixture-user/Commodity-Arbitrage-Analysis",
        description="Python and SQL analysis of commodity price arbitrage.",
        primary_language="Python",
        languages=("Python", "SQL"),
        topics=("data-analysis",),
        readme_excerpt="A commodity data analysis pipeline.",
        source_url="https://api.github.com/repos/fixture-user/Commodity-Arbitrage-Analysis",
    )
    return GitHubRepository(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_a_repository_matching_several_requirement_terms_is_offered_as_evidence() -> None:
    (evidence,) = match_repositories([repo()], ["Python", "SQL / data analysis"])

    assert evidence.repository_name == "Commodity-Arbitrage-Analysis"
    assert "Python" in evidence.matched_requirement_labels
    assert "SQL / data analysis" in evidence.matched_requirement_labels
    assert "sql" in evidence.matched_terms
    assert evidence.is_personal_project is True


def test_a_repository_matching_nothing_is_never_offered() -> None:
    evidence = match_repositories([repo()], ["Kubernetes orchestration"])

    assert evidence == []


def test_never_claims_more_than_the_content_justifies() -> None:
    """The matcher never asserts a skill LEVEL: only which terms/labels were actually found."""
    (evidence,) = match_repositories([repo()], ["Python"])

    assert evidence.matched_terms == ("python",)
    dump = str(evidence)
    assert "expert" not in dump.lower() and "score" not in dump.lower()


def test_a_fork_is_never_offered_as_evidence() -> None:
    forked = repo(is_fork=True)

    assert match_repositories([forked], ["Python"]) == []


def test_ranking_is_deterministic_by_matched_requirement_count_then_name() -> None:
    two_matches = repo(name="A-Project", description="Python and SQL work")
    one_match = repo(name="Z-Project", description="Python only", languages=("Python",))

    ranked = match_repositories([one_match, two_matches], ["Python", "SQL"])

    assert [e.repository_name for e in ranked] == ["A-Project", "Z-Project"]
    assert ranked[0].rank == 1 and ranked[1].rank == 2


def test_the_limit_caps_the_number_of_repositories_offered() -> None:
    repos = [repo(name=f"Repo-{i}", description="Python project") for i in range(5)]

    ranked = match_repositories(repos, ["Python"], limit=2)

    assert len(ranked) == 2


def test_source_url_is_kept_for_every_offered_repository() -> None:
    (evidence,) = match_repositories([repo()], ["Python"])

    assert evidence.source_url == repo().source_url
    assert evidence.url == repo().url


# --- provider gate (mirrors company_research's) --------------------------------------------


def test_disabled_by_default_gives_no_provider() -> None:
    settings = Settings(github_enabled=False, github_username="fixture-user")
    assert default_github_provider(settings, InMemorySecretStore()) is None


def test_enabled_but_missing_username_gives_no_provider() -> None:
    settings = Settings(github_enabled=True, github_username=None)
    assert default_github_provider(settings, InMemorySecretStore()) is None


def test_enabled_with_username_gives_a_real_provider_no_token_required() -> None:
    settings = Settings(github_enabled=True, github_username="fixture-user")

    provider = default_github_provider(settings, InMemorySecretStore())

    assert provider is not None and type(provider).__name__ == "GitHubClient"
