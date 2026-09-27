"""AI Provider Context / Contract (step 6): every external AI provider's position in Career-agent.

An external provider is never handed an isolated task. It receives an explicit statement of its
own role: why it is being called, what it is responsible for, what it must never decide, what
Career-agent hands it, and what Career-agent does with what comes back. This is deliberately
provider-agnostic - the same `ProviderContract` shape and rendering serves Perplexity, OpenRouter
and any future provider, so adding one means writing its contract, not inventing a new mechanism.

**Provider output is never Career-agent truth.** `Perplexity reported X` is external, sourced
information; it becomes `Career-agent evidence` only if and when Career-agent's own, existing
decision points (qualification, evidence review, personalisation) later choose to use it that way.
No contract here ever lets a provider set a business decision directly (see `docs/providers.md`).

Composition of a call's actual instructions is two layers, always kept separate in code even when
a provider's wire format has only one text field to receive them:

- SYSTEM / PROVIDER CONTEXT: `render_contract(CONTRACT)` - fixed, versioned, provider-role-wide;
- TASK CONTEXT: built fresh per call, from data Career-agent already validated for that one call.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderContract:
    """A provider's fixed position in Career-agent for one role. Declared once, next to the
    adapter that uses it; changing one is a deliberate, reviewed decision, like the audit
    whitelist (`app.services.audit.ALLOWED_DETAIL_KEYS`)."""

    name: str  # short, stable identifier, e.g. "perplexity.company_research"
    purpose: str
    responsibilities: tuple[str, ...]
    boundaries: tuple[str, ...]
    upstream_context: tuple[str, ...]  # what Career-agent hands it, for this role
    downstream_role: tuple[str, ...]  # what Career-agent does next with what comes back


def render_contract(contract: ProviderContract) -> str:
    """A compact, deterministic text block. Stable across calls: never per-call free text."""

    def section(title: str, items: tuple[str, ...]) -> str:
        return f"{title}:\n" + "\n".join(f"- {item}" for item in items)

    return "\n\n".join(
        [
            f"PURPOSE:\n{contract.purpose}",
            section("RESPONSIBILITIES", contract.responsibilities),
            section("BOUNDARIES", contract.boundaries),
            section("UPSTREAM_CONTEXT (provided by Career-agent)", contract.upstream_context),
            section("DOWNSTREAM_ROLE (what Career-agent does next)", contract.downstream_role),
        ]
    )


@dataclass(frozen=True)
class TokenUsage:
    """Shared by every provider port (`app.integrations.llm.ports`, `app.integrations.research`)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
