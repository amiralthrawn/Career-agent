"""Minimal HTTPS transport shape shared by JSON-over-HTTPS provider adapters.

Both `app.integrations.llm.openrouter` and `app.integrations.research.perplexity` speak plain
JSON-over-HTTPS via `http.client` (standard library: no new HTTP dependency). This module holds
only the small structural `Protocol`s both adapters implement against, so a fake connection
injected by a test satisfies either one without duplicating the shape twice. Each adapter keeps
its own tiny default-connection factory (binding its own host and, in OpenRouter's case, a name
an existing test already patches), so nothing here constructs a socket itself.
"""

from collections.abc import Mapping
from typing import Protocol


class HTTPResponse(Protocol):
    status: int

    def read(self) -> bytes: ...


class HTTPConnection(Protocol):
    def request(self, method: str, url: str, body: bytes, headers: Mapping[str, str]) -> None: ...
    def getresponse(self) -> HTTPResponse: ...
    def close(self) -> None: ...
