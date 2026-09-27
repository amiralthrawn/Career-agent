"""Manual smoke test of the REAL OpenRouter integration (step 5). NOT part of the pytest suite.

Runs the exact application path — PersonalizationBrief -> LLMClient -> ApplicationDraft — against
a throw-away, in-memory SQLite database, using the real services (`TargetService`,
`RequirementService`, `QualificationService`, `PersonalizationService`, `DraftService`) exactly as
the API route does, and a REAL `OpenRouterClient` reading the key from the existing `SecretStore`
(Windows Credential Manager, service "career-agent", name "openrouter_api_key").

Data is entirely SYNTHETIC and invented for this test: no real CV, no real company, no real offer,
no real contact. The candidate's first name never leaves this process — the application's context
builder (`app.services.draft_generation.build_context`) does not include it, so OpenRouter never
receives it either.

Usage (PowerShell):
    $env:OPENROUTER_MODEL = "openrouter/free"
    .venv\\Scripts\\python.exe scripts\\manual\\openrouter_smoke_test.py

    $env:OPENROUTER_MODEL = "deepseek/deepseek-v3.2"
    .venv\\Scripts\\python.exe scripts\\manual\\openrouter_smoke_test.py

Requires: the `openrouter_api_key` secret already stored (`python scripts/manage_secrets.py set
openrouter_api_key`), and `OPENROUTER_MODEL` set in the environment (NOT read from `.env`'s
committed template — export it in your shell for this run, or set it in your local, git-ignored
`.env`). This script does not set `LLM_ENABLED`: it builds the `OpenRouterClient` directly, the
same way `get_llm_client` would once that switch is on, without needing the HTTP server.

Never prints the API key. Never writes it to a file. The database is in-memory and vanishes when
the process exits.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# A generated draft may contain characters the Windows console's default codepage cannot
# encode (e.g. a non-breaking hyphen); never crash on printing a human-facing report over it.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import create_app_engine  # noqa: E402
from app.core.secrets import OPENROUTER_API_KEY, SecretStoreError, get_secret_store  # noqa: E402
from app.integrations.llm.openrouter import OpenRouterClient  # noqa: E402
from app.integrations.llm.ports import LLMError  # noqa: E402
from app.models import Base  # noqa: E402
from app.schemas.candidate import CandidateCreate  # noqa: E402
from app.schemas.drafts import DraftGenerateRequest  # noqa: E402
from app.schemas.evidence import EvidenceCreate, EvidenceLinkCreate  # noqa: E402
from app.schemas.facts import ProjectCreate, SkillCreate  # noqa: E402
from app.schemas.requirements import ManualRequirementCreate  # noqa: E402
from app.schemas.search import CriterionInput, SearchProfileCreate  # noqa: E402
from app.schemas.targets import (  # noqa: E402
    CompanyInput,
    OpportunityInput,
    SourceInput,
    TargetCreate,
)
from app.services.candidate_brain import CandidateBrainService  # noqa: E402
from app.services.draft_generation import DraftService  # noqa: E402
from app.services.personalization import PersonalizationService  # noqa: E402
from app.services.qualification import QualificationService  # noqa: E402
from app.services.requirements import RequirementService  # noqa: E402
from app.services.search_profiles import SearchProfileService  # noqa: E402
from app.services.targets import TargetService  # noqa: E402

# --- Synthetic scenario (nothing here is a real candidate, company, offer or contact) ---------

OFFER_DESCRIPTION = (
    "We are looking for a Junior Data Analyst.\n"
    "Python is required. SQL is required. pandas is required.\n"
    "Power BI is a plus. Talend is a plus.\n"
)


def build_scenario(session: Session) -> tuple[int, int]:
    """Returns (target_id, profile_id). Established: Python/SQL/pandas. Absent: Power BI, Talend."""
    brain = CandidateBrainService(session)
    brain.create_candidate(CandidateCreate(first_name="Enzo", last_name="SmokeTestFixture"))

    skill_ids = [brain.add_skill(SkillCreate(name=name)).id for name in ("Python", "SQL", "pandas")]
    project = brain.add_project(
        ProjectCreate(
            name="Sales dataset analysis",
            description="Analysed a synthetic sales dataset with pandas and Python.",
        )
    )
    # (target_type, id) pairs - never compared by raw id alone: Skill and Project ids are
    # independent sequences and can collide (e.g. both start at 1).
    facts: list[tuple[str, int]] = [("skill", sid) for sid in skill_ids]
    facts.append(("project", project.id))
    for target_type, fact_id in facts:
        # `medium` confidence -> the fact's evidence state is `known`
        # (see `derive_information_state`).
        evidence = brain.add_evidence(
            EvidenceCreate(
                source_type="document",
                source_name="Synthetic smoke-test fixture",
                confidence="medium",
            )
        )
        brain.link_evidence(
            EvidenceLinkCreate(evidence_id=evidence.id, target_type=target_type, target_id=fact_id)
        )
    # No Skill "Power BI" is ever created: its absence is the point.

    outcome = TargetService(session).create_target(
        TargetCreate(
            company=CompanyInput(
                name="Example Data SAS",
                sector="Data / software",
                location="Paris",
                country_code="FR",
            ),
            opportunity=OpportunityInput(
                title="Junior Data Analyst", description_text=OFFER_DESCRIPTION
            ),
            source=SourceInput(kind="manual", label="Synthetic smoke-test scenario"),
        )
    )
    target_id = outcome.target.id
    requirements = RequirementService(session)
    requirements.extract(target_id, actor="cli")
    # "Talend" is not in the deterministic skill taxonomy (step 3b): entered the way the
    # application already supports for anything outside it - a manual requirement, with its
    # exact excerpt and source - rather than by patching taxonomy data for this script.
    requirements.add_manual(
        target_id,
        ManualRequirementCreate(
            label="Talend",
            importance="nice_to_have",
            excerpt="Talend is a plus.",
            source=SourceInput(
                kind="manual",
                label="Synthetic smoke-test scenario",
                reference="offer text, noted manually",
            ),
        ),
    )

    profile = SearchProfileService(session).create_profile(
        SearchProfileCreate(
            name="Smoke test profile",
            criteria=[CriterionInput(dimension="sector", values=["Data"], level="preferred")],
        )
    )
    QualificationService(session).qualify(target_id, profile.id)
    return target_id, profile.id


def resolve_client(model: str) -> OpenRouterClient:
    try:
        store = get_secret_store()
    except SecretStoreError as error:
        print(f"SecretStore unavailable: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if not store.exists(OPENROUTER_API_KEY):
        print(
            "No 'openrouter_api_key' secret found. Set it first:\n"
            "    python scripts/manage_secrets.py set openrouter_api_key",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return OpenRouterClient(store, model, referer="https://career-agent.local/smoke-test")


def main() -> None:
    model = os.environ.get("OPENROUTER_MODEL")
    if not model:
        print(
            "Set OPENROUTER_MODEL in the environment before running this script.", file=sys.stderr
        )
        raise SystemExit(1)
    llm = resolve_client(model)

    engine = create_app_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        target_id, profile_id = build_scenario(session)

        brief = PersonalizationService(session).brief(target_id, profile_id)
        established = sorted({s.requirement.label for s in brief.strengths})
        absent = sorted({d.requirement.label for d in brief.do_not_claim})
        print(f"PersonalizationBrief: strengths={established} do_not_claim={absent}")

        started = time.monotonic()
        try:
            draft = DraftService(session, llm).generate(
                target_id, DraftGenerateRequest(model=model), actor="cli"
            )
        except Exception as error:  # noqa: BLE001 - a smoke test reports any failure, unfiltered
            elapsed_ms = round((time.monotonic() - started) * 1000)
            code = error.__cause__.code.value if isinstance(error.__cause__, LLMError) else None
            print(
                json.dumps(
                    {
                        "requested_model": model,
                        "status": "error",
                        "error_code": code,
                        "duration_ms": elapsed_ms,
                    },
                    indent=2,
                )
            )
            raise SystemExit(1) from None
        elapsed_ms = round((time.monotonic() - started) * 1000)

    report = {
        "requested_model": model,
        "returned_model": draft.model,
        "status": draft.status.value,
        "duration_ms": elapsed_ms,
        "reported_duration_ms": draft.duration_ms,
        "output_chars": len(draft.body),
        "usage_prompt_tokens": draft.usage_prompt_tokens,
        "usage_completion_tokens": draft.usage_completion_tokens,
        "claims": [c["requirement"] for c in draft.claims],
        "warnings": [w["requirement"] for w in draft.warnings],
        "subject": draft.subject,
    }
    print(json.dumps(report, indent=2))
    print("\n--- body ---\n" + draft.body + "\n--- end body ---")


if __name__ == "__main__":
    main()
