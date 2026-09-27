"""Company research (step 6): Career-agent -> ResearchQuery -> Perplexity -> ResearchResult.

**Career-agent decides the research OBJECTIVE; the provider never does.** This service builds the
query from data already in Career-agent's own `Company` record (never candidate data), calls the
injected `ResearchProvider`, and returns its result AS IS - a set of external, sourced
observations, not a decision. Nothing here writes to the database, changes a `Target`'s status,
touches a `Qualification`, or sends anything: turning a `ResearchResult` into Career-agent
evidence is a separate, later, deliberate choice this service does not make (see
`docs/providers.md`).
"""

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.core.secrets import PERPLEXITY_API_KEY, SecretStore, SecretStoreError
from app.integrations.research.perplexity import PerplexityClient
from app.integrations.research.ports import (
    MAX_RESULTS_LIMIT,
    ResearchProvider,
    ResearchQuery,
    ResearchResult,
    ResearchSubject,
)
from app.repositories.targets import get_company

DEFAULT_MAX_RESULTS = 10


def default_research_provider(settings: Settings, secrets: SecretStore) -> ResearchProvider | None:
    """`None` by default: company research is then refused, explicitly, before anything runs.

    Mirrors `app.api.drafts.get_llm_client` exactly: a master switch (`RESEARCH_ENABLED`, default
    `false`), a configured preset (`PERPLEXITY_PRESET`), and the secret all have to be present.
    """
    if not settings.research_enabled or not settings.perplexity_preset:
        return None
    try:
        if not secrets.exists(PERPLEXITY_API_KEY):
            return None
    except SecretStoreError:
        return None  # fail closed: no secure backend, no provider
    return PerplexityClient(secrets, settings.perplexity_preset)


class CompanyResearchService:
    def __init__(self, session: Session, provider: ResearchProvider | None) -> None:
        self._session = session
        self._provider = provider

    def research(
        self,
        company_id: int,
        *,
        objective: str,
        focus_areas: tuple[str, ...] = (),
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> ResearchResult:
        if self._provider is None:
            raise UnprocessableError("No research provider is configured")
        if max_results < 1 or max_results > MAX_RESULTS_LIMIT:
            raise UnprocessableError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
        company = get_company(self._session, company_id)
        if company is None:
            raise NotFoundError(f"Company {company_id} not found")

        query = ResearchQuery(
            objective=objective,
            subject=ResearchSubject(
                company_name=company.name,
                website=company.website_url,
                location=company.location,
                sector=company.sector,
            ),
            focus_areas=focus_areas,
            max_results=max_results,
        )
        # The provider's return value is handed back UNCHANGED: no field of it is read here to
        # make a decision, and nothing is persisted. See module docstring.
        return self._provider.research(query)
