"""LLMClient: what the application needs from any text-generation provider.

**The LLM is a generator, never a source of truth.** It never touches the database, never calls
a tool, and never decides that a fact exists. `LLMClient.generate` accepts a `GenerationRequest`
built ENTIRELY by the application (a fixed system prompt plus a bounded, JSON-serialisable
context assembled from already-validated data) and returns a `GenerationResult`: free text, to be
reviewed by a human before it is used for anything. Nothing here exposes OpenRouter (or any other
provider) to the rest of the application: callers depend only on this port.

Failure is explicit: a provider that cannot complete a call raises `LLMError(code)` (only the
code is kept, never a message or the request/response payload). It is never turned into a
silently empty or fabricated result.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.models.enums import LLMCallStatus, LLMErrorCode

MAX_OUTPUT_TOKENS_LIMIT = 2000
MIN_TEMPERATURE, MAX_TEMPERATURE = 0.0, 1.0
DEFAULT_MAX_OUTPUT_TOKENS = 600
DEFAULT_TEMPERATURE = 0.2  # low: the point is a grounded draft, not creative variation


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    """Everything the application decided to expose to the model; nothing else is available."""

    task: str  # short label of what is being generated, e.g. "application_email"
    system: str  # fixed system instructions (a versioned constant, never per-call free text)
    context: Mapping[str, Any]  # bounded, JSON-serialisable facts already validated by the app
    model: str | None = None  # requested model id; None = the client's own default
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    temperature: float = DEFAULT_TEMPERATURE

    def __post_init__(self) -> None:
        if not 1 <= self.max_output_tokens <= MAX_OUTPUT_TOKENS_LIMIT:
            raise ValueError(f"max_output_tokens must be between 1 and {MAX_OUTPUT_TOKENS_LIMIT}")
        if not MIN_TEMPERATURE <= self.temperature <= MAX_TEMPERATURE:
            raise ValueError(f"temperature must be between {MIN_TEMPERATURE} and {MAX_TEMPERATURE}")


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model: str
    status: LLMCallStatus = LLMCallStatus.OK
    usage: TokenUsage | None = None
    duration_ms: int | None = None


class LLMError(Exception):
    """A call could not be completed. Carries only a CODE: never a message or any payload."""

    def __init__(self, code: LLMErrorCode = LLMErrorCode.OTHER) -> None:
        super().__init__(code.value)
        self.code = code


class LLMClient(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...
