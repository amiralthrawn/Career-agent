"""Manual smoke test of the REAL Perplexity sourcing adapter (step 3c). NOT part of the pytest
suite.

Demonstrates: SearchProfile-shaped criteria -> Perplexity (Agent API, web_search tool) ->
SearchHit(s) -> which ones carry a stated `company_name`/`offer_title` attribute (and would
therefore become a Target through `HitExtractor`) versus which are correctly rejected for lack of
one. No database is touched: this only exercises `PerplexityWebSearchProvider.search()` directly,
the same way `default_web_search_provider` would once `RESEARCH_ENABLED=true`.

No candidate data is ever involved: the query is built only from generic, harmless search terms
(a role, a city, a contract type) - never from a real candidate's profile.

Usage (PowerShell):
    $env:PERPLEXITY_PRESET = "low"
    .venv\\Scripts\\python.exe scripts\\manual\\perplexity_sourcing_smoke_test.py [offers|companies]

Requires: the `perplexity_api_key` secret already stored (`python scripts/manage_secrets.py set
perplexity_api_key`). Never prints the key. Never writes it to a file.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from app.core.secrets import PERPLEXITY_API_KEY, SecretStoreError, get_secret_store  # noqa: E402
from app.integrations.sourcing.perplexity import PerplexityWebSearchProvider  # noqa: E402
from app.integrations.sourcing.ports import (  # noqa: E402
    ProviderError,
    QueryCriterion,
    SearchQuery,
)
from app.models.enums import (  # noqa: E402
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    EmploymentType,
    SourcingMode,
)

# Generic, harmless search terms - never a real candidate's own data.
CRITERIA = (
    QueryCriterion(
        CriterionDimension.KEYWORD,
        CriterionOperator.ANY_OF,
        ("data analyst",),
        CriterionLevel.REQUIRED,
    ),
    QueryCriterion(
        CriterionDimension.LOCATION, CriterionOperator.ANY_OF, ("Paris",), CriterionLevel.PREFERRED
    ),
)
MAX_RESULTS = 5


def resolve_provider(preset: str) -> PerplexityWebSearchProvider:
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
    return PerplexityWebSearchProvider(store, preset)


def main() -> None:
    preset = os.environ.get("PERPLEXITY_PRESET")
    if not preset:
        print(
            "Set PERPLEXITY_PRESET in the environment before running this script.", file=sys.stderr
        )
        raise SystemExit(1)
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "offers"
    mode = SourcingMode.OFFERS if mode_arg == "offers" else SourcingMode.COMPANIES

    provider = resolve_provider(preset)
    query = SearchQuery(
        mode=mode,
        profile_id=0,
        criteria=CRITERIA,
        max_results=MAX_RESULTS,
        contract_type=EmploymentType.APPRENTICESHIP if mode is SourcingMode.OFFERS else None,
    )

    try:
        result = provider.search(query)
    except ProviderError as error:
        print(json.dumps({"mode": mode.value, "status": "error", "error_code": error.code.value}))
        raise SystemExit(1) from None

    report = {
        "mode": mode.value,
        "preset": preset,
        "completed": result.completed,
        "hit_count": len(result.hits),
        "sources_consulted": list(result.sources_consulted),
        "hits": [
            {
                "url": hit.url,
                "title": hit.title,
                "stated_company_name": hit.attributes.get("company_name"),
                "stated_offer_title": hit.attributes.get("offer_title"),
                "would_be_ingested": "company_name" in hit.attributes
                and (mode is SourcingMode.COMPANIES or "offer_title" in hit.attributes),
            }
            for hit in result.hits
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    usable = sum(1 for hit in report["hits"] if hit["would_be_ingested"])  # type: ignore[index]
    print(f"\n{usable}/{len(result.hits)} hit(s) carried a stated identity and would be ingested.")
    print("Reminder: no database was touched by this script.")


if __name__ == "__main__":
    main()
