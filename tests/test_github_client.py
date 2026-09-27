"""GitHubClient: HTTP transport is a fake (no socket, no real network, no real token)."""

import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pytest

from app.core.secrets import GITHUB_TOKEN, InMemorySecretStore
from app.integrations.github.client import GitHubClient
from app.integrations.github.ports import GitHubError, GitHubErrorCode, GitHubStatus


@dataclass
class FakeResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass
class ScriptedConnection:
    responses: dict[str, tuple[int, bytes]]
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    _path: str = ""
    _headers: Mapping[str, str] = field(default_factory=dict)

    def request(self, method: str, url: str, body: bytes, headers: Mapping[str, str]) -> None:
        self._path = url
        self._headers = headers
        self.calls.append((url, dict(headers)))

    def getresponse(self) -> FakeResponse:
        status, body = self.responses.get(self._path, (404, b'{"message": "Not Found"}'))
        return FakeResponse(status, body)

    def close(self) -> None:
        pass


def scripted(
    responses: dict[str, tuple[int, bytes]],
) -> tuple[ScriptedConnection, Callable[[], ScriptedConnection]]:
    connection = ScriptedConnection(responses)

    def connect() -> ScriptedConnection:
        return connection

    return connection, connect


def repo_entry(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "name": "Commodity-Arbitrage-Analysis",
        "html_url": "https://github.com/fixture-user/Commodity-Arbitrage-Analysis",
        "url": "https://api.github.com/repos/fixture-user/Commodity-Arbitrage-Analysis",
        "description": "Python/SQL analysis of commodity price arbitrage.",
        "language": "Python",
        "topics": ["python", "sql", "data-analysis"],
        "fork": False,
    }
    return {**defaults, **overrides}


def readme_body(text: str = "# Commodity Arbitrage\n\nSQL-backed data pipeline.") -> bytes:
    return json.dumps(
        {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    ).encode()


def test_research_returns_public_non_fork_repositories_with_languages_and_readme() -> None:
    connection, connect = scripted(
        {
            "/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (
                200,
                json.dumps([repo_entry()]).encode(),
            ),
            "/repos/fixture-user/Commodity-Arbitrage-Analysis/languages": (
                200,
                json.dumps({"Python": 1000, "SQL": 200}).encode(),
            ),
            "/repos/fixture-user/Commodity-Arbitrage-Analysis/readme": (200, readme_body()),
        }
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    result = client.research("fixture-user")

    assert result.status is GitHubStatus.OK
    (repo,) = result.repositories
    assert repo.name == "Commodity-Arbitrage-Analysis"
    assert repo.primary_language == "Python"
    assert set(repo.languages) == {"Python", "SQL"}
    assert "sql" in repo.topics
    assert repo.readme_excerpt is not None and "SQL-backed" in repo.readme_excerpt
    assert repo.is_fork is False
    assert repo.source_url.startswith("https://api.github.com/")
    assert connection.calls  # a request was actually made through the injected transport


def test_a_fork_is_never_returned() -> None:
    _connection, connect = scripted(
        {
            "/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (
                200,
                json.dumps([repo_entry(fork=True)]).encode(),
            ),
        }
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    result = client.research("fixture-user")

    assert result.repositories == ()
    assert result.status is GitHubStatus.NO_RESULTS


def test_no_public_repositories_gives_no_results_not_an_error() -> None:
    _connection, connect = scripted(
        {
            "/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (
                200,
                b"[]",
            ),
        }
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    result = client.research("fixture-user")

    assert result.status is GitHubStatus.NO_RESULTS
    assert result.repositories == ()


def test_a_missing_readme_never_fails_the_repository() -> None:
    _connection, connect = scripted(
        {
            "/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (
                200,
                json.dumps([repo_entry()]).encode(),
            ),
            "/repos/fixture-user/Commodity-Arbitrage-Analysis/languages": (200, b"{}"),
            "/repos/fixture-user/Commodity-Arbitrage-Analysis/readme": (
                404,
                b'{"message": "Not Found"}',
            ),
        }
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    result = client.research("fixture-user")

    (repo,) = result.repositories
    assert repo.readme_excerpt is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (404, GitHubErrorCode.NOT_FOUND),
        (403, GitHubErrorCode.RATE_LIMITED),
        (500, GitHubErrorCode.UNAVAILABLE),
    ],
)
def test_status_codes_map_to_stable_error_codes(status: int, code: GitHubErrorCode) -> None:
    _connection, connect = scripted(
        {"/users/missing-user/repos?type=owner&sort=updated&per_page=100": (status, b"{}")}
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    with pytest.raises(GitHubError) as excinfo:
        client.research("missing-user")
    assert excinfo.value.code is code


def test_an_optional_token_is_sent_when_configured_never_required() -> None:
    secrets = InMemorySecretStore()
    secrets.set(GITHUB_TOKEN, "ghp_fixture_not_real")  # noqa: S105 - synthetic test value
    connection, connect = scripted(
        {"/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (200, b"[]")}
    )
    client = GitHubClient(secrets, connect=connect)

    client.research("fixture-user")

    (_url, headers) = connection.calls[0]
    assert headers["Authorization"] == "Bearer ghp_fixture_not_real"


def test_no_token_configured_still_works() -> None:
    connection, connect = scripted(
        {"/users/fixture-user/repos?type=owner&sort=updated&per_page=100": (200, b"[]")}
    )
    client = GitHubClient(InMemorySecretStore(), connect=connect)

    client.research("fixture-user")

    (_url, headers) = connection.calls[0]
    assert "Authorization" not in headers
