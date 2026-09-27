"""GitHubClient: the real adapter for `GitHubProvider` (step 9).

Reads ONLY public data (a user's public, non-fork repositories and their README), via GitHub's
public REST API, over the SAME injectable HTTP transport already shared by OpenRouter and
Perplexity (`app.integrations.http_transport`) - no new HTTP dependency. An optional token
(`github_token` secret) raises the unauthenticated rate limit; it is never required.

Verified against the real API: never in pytest (`no_network` fixture / `FORBIDDEN_IMPORTS` scan),
only ever through a manual, non-pytest script - see step 6's Perplexity precedent.
"""

import base64
import json
from collections.abc import Callable
from http.client import HTTPSConnection

from app.core.secrets import GITHUB_TOKEN, SecretStore, SecretStoreError
from app.integrations.github.ports import (
    GitHubError,
    GitHubErrorCode,
    GitHubRepository,
    GitHubResult,
    GitHubStatus,
)
from app.integrations.http_transport import HTTPConnection

API_HOST = "api.github.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_REPOS = 20
MAX_README_CHARS = 1000
USER_AGENT = "career-agent (local, personal use)"


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(API_HOST, timeout=timeout)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


class GitHubClient:
    def __init__(
        self,
        secrets: SecretStore,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Callable[[], HTTPConnection] | None = None,
    ) -> None:
        self._secrets = secrets
        self._connect = connect or _default_connect(timeout)

    def research(self, username: str) -> GitHubResult:
        repos_payload = self._get(f"/users/{username}/repos?type=owner&sort=updated&per_page=100")
        if not isinstance(repos_payload, list):
            raise GitHubError(GitHubErrorCode.INVALID_RESPONSE)
        repositories = []
        for entry in repos_payload[:MAX_REPOS]:
            if not isinstance(entry, dict) or entry.get("fork"):
                continue  # a fork is not the candidate's own work
            repositories.append(self._repository(username, entry))
        status = GitHubStatus.OK if repositories else GitHubStatus.NO_RESULTS
        return GitHubResult(username=username, status=status, repositories=tuple(repositories))

    def _repository(self, username: str, entry: dict[str, object]) -> GitHubRepository:
        name = str(entry.get("name", ""))
        languages = self._languages(username, name)
        topics = entry.get("topics")
        return GitHubRepository(
            name=name,
            url=str(entry.get("html_url", "")),
            description=_str_or_none(entry.get("description")),
            primary_language=_str_or_none(entry.get("language")),
            languages=languages,
            topics=tuple(t for t in topics if isinstance(t, str))
            if isinstance(topics, list)
            else (),
            readme_excerpt=self._readme(username, name),
            is_fork=bool(entry.get("fork", False)),
            source_url=str(entry.get("url", f"https://api.github.com/repos/{username}/{name}")),
        )

    def _languages(self, username: str, repo: str) -> tuple[str, ...]:
        try:
            data = self._get(f"/repos/{username}/{repo}/languages")
        except GitHubError:
            return ()
        return tuple(data) if isinstance(data, dict) else ()

    def _readme(self, username: str, repo: str) -> str | None:
        try:
            data = self._get(f"/repos/{username}/{repo}/readme")
        except GitHubError:
            return None  # a missing README never fails the whole repository
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        content = data.get("content")
        if not isinstance(content, str):
            return None
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return None
        return text[:MAX_README_CHARS]

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
        try:
            token = self._secrets.get(GITHUB_TOKEN)
        except SecretStoreError:
            token = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str) -> object:
        connection = self._connect()
        try:
            connection.request("GET", path, body=b"", headers=self._headers())
            response = connection.getresponse()
            content = response.read()
        except TimeoutError:
            raise GitHubError(GitHubErrorCode.TIMEOUT) from None
        except OSError:
            raise GitHubError(GitHubErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()
        _raise_for_status(response.status)
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            raise GitHubError(GitHubErrorCode.INVALID_RESPONSE) from None


def _raise_for_status(status: int) -> None:
    if status < 400:
        return
    if status == 404:
        raise GitHubError(GitHubErrorCode.NOT_FOUND)
    if status == 403 or status == 429:
        raise GitHubError(GitHubErrorCode.RATE_LIMITED)
    if status >= 500:
        raise GitHubError(GitHubErrorCode.UNAVAILABLE)
    raise GitHubError(GitHubErrorCode.OTHER)
