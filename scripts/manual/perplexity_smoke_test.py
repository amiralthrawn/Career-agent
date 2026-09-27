"""Manual smoke test of the REAL Perplexity integration (step 6). NOT part of the pytest suite.

Demonstrates: Career-agent context -> ResearchQuery -> Perplexity -> ResearchResult -> provenance.

Uses the real `PerplexityClient`, reading the key from the existing `SecretStore` (Windows
Credential Manager, service "career-agent", name "perplexity_api_key"). The subject is a public,
non-sensitive, real company chosen only because it is well documented and harmless to look up
(no partnership, endorsement or business relationship with Career-agent is implied). No candidate
data, no CV, no private information, no e-mail is ever involved.

The result stays what it is: an external, sourced research result. Nothing here turns it into
Career-agent evidence, a qualification, or a decision - that boundary is enforced by
`CompanyResearchService` itself (see `docs/providers.md`), not worked around by this script.

Usage (PowerShell):
    $env:PERPLEXITY_PRESET = "low"
    .venv\\Scripts\\python.exe scripts\\manual\\perplexity_smoke_test.py

Requires: the `perplexity_api_key` secret already stored (`python scripts/manage_secrets.py set
perplexity_api_key`). This script does not set `RESEARCH_ENABLED`: it builds the `PerplexityClient`
directly, the same way `default_research_provider` would once that switch is on.

Never prints the API key. Never writes it to a file.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import create_app_engine  # noqa: E402
from app.core.secrets import PERPLEXITY_API_KEY, SecretStoreError, get_secret_store  # noqa: E402
from app.integrations.research.perplexity import PerplexityClient  # noqa: E402
from app.integrations.research.ports import ResearchError  # noqa: E402
from app.models import Base  # noqa: E402
from app.schemas.targets import CompanyInput  # noqa: E402
from app.services.companies import CompanyService  # noqa: E402
from app.services.company_research import CompanyResearchService  # noqa: E402
from app.services.sources import Provenance, manual_spec  # noqa: E402

# A real, public, well-documented, harmless company: nothing private or sensitive about it.
SUBJECT = CompanyInput(
    name="Wikimedia Foundation",
    website_url="https://wikimediafoundation.org",
    location="San Francisco",
    sector="Non-profit / online knowledge",
)
OBJECTIVE = "Understand the organisation's activity, technology environment and recent news"
FOCUS_AREAS = ("technology environment", "recent news and developments")


def resolve_client(preset: str) -> PerplexityClient:
    try:
        store = get_secret_store()
    except SecretStoreError as error:
        print(f"SecretStore unavailable: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if not store.exists(PERPLEXITY_API_KEY):
        print(
            "No 'perplexity_api_key' secret found. Set it first:\n"
            "    python scripts/manage_secrets.py set perplexity_api_key",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return PerplexityClient(store, preset)


def main() -> None:
    preset = os.environ.get("PERPLEXITY_PRESET")
    if not preset:
        print(
            "Set PERPLEXITY_PRESET in the environment before running this script.", file=sys.stderr
        )
        raise SystemExit(1)
    client = resolve_client(preset)

    engine = create_app_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        # Only a Company is needed for company research; reuses the existing find-or-create
        # service directly rather than going through the unrelated Target-creation flow.
        provenance = Provenance(session)
        company = (
            CompanyService(session, provenance)
            .find_or_create(SUBJECT, manual_spec(label="Smoke-test scenario"))
            .entity
        )
        session.commit()
        company_id = company.id

        try:
            result = CompanyResearchService(session, client).research(
                company_id, objective=OBJECTIVE, focus_areas=FOCUS_AREAS, max_results=5
            )
        except ResearchError as error:
            print(
                json.dumps(
                    {"preset": preset, "status": "error", "error_code": error.code.value}, indent=2
                )
            )
            raise SystemExit(1) from None

    report = {
        "preset_requested": preset,
        "model_returned": result.model,
        "status": result.status.value,
        "duration_ms": result.duration_ms,
        "usage_prompt_tokens": result.usage.prompt_tokens if result.usage else None,
        "usage_completion_tokens": result.usage.completion_tokens if result.usage else None,
        "observation_count": len(result.observations),
        "sources": [
            {"url": o.source_url, "title": o.source_title, "published": o.published}
            for o in result.observations
        ],
    }
    print(json.dumps(report, indent=2))
    print("\n--- summary (provider narrative, not per-fact sourced) ---")
    print(result.summary or "(none)")
    print("\n--- observations (each with its own source) ---")
    for index, observation in enumerate(result.observations, start=1):
        print(f"\n[{index}] claim: {observation.claim}")
        print(f"    source: {observation.source_title} <{observation.source_url}>")
        if observation.excerpt:
            print(f"    excerpt: {observation.excerpt}")
    print(
        "\nReminder: this is an EXTERNAL, SOURCED OBSERVATION, not Career-agent-verified evidence."
    )


if __name__ == "__main__":
    main()
