"""Deterministic FakeLLMClient for the tests. Never touches the network. No real model involved."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.integrations.llm.ports import (
    GenerationRequest,
    GenerationResult,
    LLMError,
    TokenUsage,
)
from app.models.enums import LLMCallStatus


def default_body(request: GenerationRequest) -> str:
    """Echoes ONLY what is in `context["strengths"]`: cannot invent by construction.

    A deterministic stand-in for a real model that would follow `request.system`'s instructions.
    """
    context = request.context
    lines = [f"Dear Hiring Team at {context['company_name']},", ""]
    if context.get("offer_title"):
        lines.append(f"I am applying for the {context['offer_title']} position.")
    else:
        lines.append("I would like to express my interest in future opportunities.")
    for strength in context.get("strengths", []):
        facts = ", ".join(f"{fact['name']} ({fact['state']})" for fact in strength["facts"])
        line = f"- {strength['requirement']}"
        lines.append(f"{line}: {facts}" if facts else line)
    lines += ["", "Best regards,"]
    return "\n".join(lines)


@dataclass
class FakeLLMClient:
    """Configurable, deterministic. `text` overrides the default (context-only) body builder."""

    text: Callable[[GenerationRequest], str] = default_body
    model: str = "fake-llm-v1"
    status: LLMCallStatus = LLMCallStatus.OK
    usage: TokenUsage | None = field(default_factory=lambda: TokenUsage(120, 80))
    duration_ms: int | None = 42
    raises: LLMError | None = None
    requests: list[GenerationRequest] = field(default_factory=list)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return GenerationResult(
            text=self.text(request),
            model=self.model,
            status=self.status,
            usage=self.usage,
            duration_ms=self.duration_ms,
        )


def forbidden_mention(label: str) -> Callable[[GenerationRequest], str]:
    """A misbehaving body that leaks a forbidden label - used to test the detection guard."""

    def build(request: GenerationRequest) -> str:
        return f"{default_body(request)}\nI have solid experience with {label}."

    return build


def echo_context(request: GenerationRequest) -> str:
    """A body that dumps the raw context, so a test can check exactly what the model was told."""
    return json.dumps(request.context, sort_keys=True)


def constant(text: str) -> Callable[[GenerationRequest], Any]:
    return lambda _request: text
