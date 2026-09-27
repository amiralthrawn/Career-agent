"""Contact research (step 8): search a `ResearchProvider` for a professional contact of a
requested category, at a company, for one target - never a decision, only sourced observations
a human must review before anything becomes a `Contact`.

**Career-agent decides who to search for; Perplexity never decides who to contact.** The query
built here never carries candidate data (no criteria, no skills, no "should this person apply"):
only the company (via the existing, unchanged `ResearchSubject`) and the CATEGORY of contact
wanted (`RoleCategory`). The result is stored, verbatim and unaccepted, as
`ContactResearchObservation` rows (see that module) - never auto-promoted into a `Contact`.

Reuses `app.services.company_research.default_research_provider` as-is: the same
`RESEARCH_ENABLED`/`PERPLEXITY_PRESET`/secret gate serves both use cases, since both call the same
Perplexity `ResearchProvider`.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.contacts.ports import FoundChannel, FoundContact
from app.integrations.research.ports import (
    ResearchProvider,
    ResearchQuery,
    ResearchResult,
    ResearchSubject,
)
from app.models import Company, ContactResearchObservation, TargetContact
from app.models.enums import InfoStatus, ProposalStatus, RoleCategory, SourceKind
from app.models.sources import SourceSpec
from app.repositories import candidate_brain as brain_repo
from app.repositories.targets import (
    claim_observation_pending,
    existing_fingerprints,
    get_company,
    get_link,
    get_target,
    stage,
)
from app.repositories.targets import get_observation as repo_get_observation
from app.repositories.targets import list_observations as repo_list_observations
from app.schemas.contact_research import ContactObservationAccept, ContactObservationReject
from app.services.contacts import ContactService
from app.services.sources import Provenance

DEFAULT_MAX_RESULTS = 8

# Only categories a search can meaningfully be framed for; `UNKNOWN` is not a request, it is the
# absence of one.
ROLE_LABEL: dict[RoleCategory, str] = {
    RoleCategory.RECRUITER: "recruiter or talent acquisition contact",
    RoleCategory.HR: "human resources contact",
    RoleCategory.MANAGER: "hiring manager or team lead for the relevant team",
    RoleCategory.TECH: "technical lead or engineering manager",
    RoleCategory.FOUNDER: "founder or executive contact",
    RoleCategory.OTHER: "other relevant professional contact",
}

RESEARCH_OBJECTIVE = (
    "Identify a publicly documented professional contact at the company matching the requested "
    "role. Report their name, title and any publicly published professional contact address "
    "only when explicitly stated in a public source. Never guess, infer or complete a name or an "
    "address; never decide whether the candidate should apply."
)


def fingerprint(source_url: str, claim: str) -> str:
    """Stable hash of what was reported, so a repeated batch run never stores the same finding
    for the same target twice - the idempotence guarantee `app.services.research_planning` uses
    for company facts, applied here per-target instead of per-company."""
    return hashlib.sha256(f"{source_url}\n{claim}".encode()).hexdigest()


def build_query(
    company: Company, role_category: RoleCategory, *, max_results: int = DEFAULT_MAX_RESULTS
) -> ResearchQuery:
    if role_category not in ROLE_LABEL:
        raise UnprocessableError(f"{role_category.value} is not a valid search category")
    return ResearchQuery(
        objective=RESEARCH_OBJECTIVE,
        subject=ResearchSubject(
            company_name=company.name,
            website=company.website_url,
            location=company.location,
            sector=company.sector,
        ),
        focus_areas=(ROLE_LABEL[role_category],),
        max_results=max_results,
    )


def store_observations(
    session: Session,
    *,
    target_id: int,
    company_id: int,
    role_category: RoleCategory,
    result: ResearchResult,
) -> list[ContactResearchObservation]:
    """Persist each SOURCED observation as a PENDING proposal - never a `Contact`, never twice for
    the same target. An observation without a `source_url` is dropped, same "no source, no fact"
    rule as `app.services.research_planning.accept_observations`."""
    already = existing_fingerprints(session, target_id)
    created: list[ContactResearchObservation] = []
    for observation in result.observations:
        if not observation.source_url:
            continue
        mark = fingerprint(observation.source_url, observation.claim)
        if mark in already:
            continue
        already.add(mark)
        created.append(
            stage(
                session,
                ContactResearchObservation(
                    target_id=target_id,
                    company_id=company_id,
                    requested_role_category=role_category,
                    claim=observation.claim,
                    source_url=observation.source_url,
                    source_title=observation.source_title,
                    excerpt=observation.excerpt,
                    published=observation.published,
                    retrieved_at=result.retrieved_at,
                    fingerprint=mark,
                ),
            )
        )
    return created


class ContactResearchService:
    """The single-target, on-demand path (an API call for "search now for this application").

    One `ResearchQuery` PER requested category, each tagged unambiguously on its resulting
    observations: a combined multi-category query would leave no way to know, without guessing,
    which category a returned observation actually answers - and this module never guesses.
    """

    def __init__(self, session: Session, provider: ResearchProvider | None) -> None:
        self._session = session
        self._provider = provider

    def search(
        self, target_id: int, role_categories: Sequence[RoleCategory]
    ) -> list[ContactResearchObservation]:
        if self._provider is None:
            raise UnprocessableError("No research provider is configured")
        if not role_categories:
            raise UnprocessableError("At least one role category is required")
        candidate_id = self._candidate_id()
        target = get_target(self._session, candidate_id, target_id)
        if target is None:
            raise NotFoundError(f"Target {target_id} not found")
        company = get_company(self._session, target.company_id)
        if company is None:
            raise NotFoundError(f"Company {target.company_id} not found")

        created: list[ContactResearchObservation] = []
        for role_category in role_categories:
            query = build_query(company, role_category)
            result = self._provider.research(query)
            created.extend(
                store_observations(
                    self._session,
                    target_id=target.id,
                    company_id=company.id,
                    role_category=role_category,
                    result=result,
                )
            )
        self._session.commit()
        return created

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- review (Part 5, human-in-the-loop) --------------------------------------------

    def list_observations(
        self,
        *,
        target_id: int | None = None,
        company_id: int | None = None,
        status: ProposalStatus | None = None,
    ) -> Sequence[ContactResearchObservation]:
        return repo_list_observations(
            self._session, target_id=target_id, company_id=company_id, status=status
        )

    def get_observation(self, observation_id: int) -> ContactResearchObservation:
        observation = repo_get_observation(self._session, observation_id)
        if observation is None:
            raise NotFoundError(f"Observation {observation_id} not found")
        return observation

    def reject(
        self, observation_id: int, decision: ContactObservationReject
    ) -> ContactResearchObservation:
        """Reject a pending observation. Creates no contact and no channel."""
        observation = self.get_observation(observation_id)
        self._require_pending(observation)
        if not claim_observation_pending(self._session, observation.id, ProposalStatus.REJECTED):
            raise ConflictError("The observation has already been decided")
        observation.review_note = decision.note
        observation.decided_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(observation)
        return observation

    def accept(
        self, observation_id: int, decision: ContactObservationAccept
    ) -> ContactResearchObservation:
        """Human validation: the human's OWN confirmed fields become a `Contact`, through the
        existing, unmodified `ContactService.record_contact` - never a second write path, and
        never a value read straight out of `claim`/`excerpt` without the human retyping it here.
        """
        observation = self.get_observation(observation_id)
        self._require_pending(observation)
        company = get_company(self._session, observation.company_id)
        if company is None:
            raise NotFoundError(f"Company {observation.company_id} not found")

        # From here on everything happens in a single transaction.
        if not claim_observation_pending(self._session, observation.id, ProposalStatus.ACCEPTED):
            raise ConflictError("The observation has already been decided")

        source = SourceSpec(
            kind=SourceKind.PUBLIC_PAGE,
            label=observation.source_title or observation.source_url,
            url=observation.source_url,
        )
        channels: tuple[FoundChannel, ...] = ()
        if decision.channel_kind is not None and decision.channel_value is not None:
            channels = (
                FoundChannel(
                    kind=decision.channel_kind,
                    value=decision.channel_value,
                    source=source,
                    status=InfoStatus.FOUND,
                ),
            )
        found = FoundContact(
            source=source,
            full_name=decision.full_name,
            is_generic=decision.is_generic,
            role_title=decision.role_title,
            role_category=decision.role_category or observation.requested_role_category,
            status=InfoStatus.FOUND,
            channels=channels,
        )
        resolution = ContactService(self._session, Provenance(self._session)).record_contact(
            company, found
        )

        # Link to the target that prompted the search (Part 4): reuses `TargetContact` as-is,
        # never overriding an existing primary contact.
        if get_link(self._session, observation.target_id, resolution.contact.id) is None:
            stage(
                self._session,
                TargetContact(
                    target_id=observation.target_id,
                    contact_id=resolution.contact.id,
                    company_id=observation.company_id,
                    is_primary=False,
                ),
            )

        observation.reviewed_data = decision.model_dump(mode="json")
        observation.resulting_contact_id = resolution.contact.id
        observation.review_note = decision.note
        observation.decided_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(observation)
        return observation

    @staticmethod
    def _require_pending(observation: ContactResearchObservation) -> None:
        if observation.status is not ProposalStatus.PENDING:
            raise ConflictError(
                f"The observation has already been decided ({observation.status.value})"
            )
