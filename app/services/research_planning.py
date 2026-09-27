"""Information needs and research plans (step 7): decide WHY and WHETHER to research, before ever
calling Perplexity - never "Perplexity should look into something", always a precise reason tied
to one stored `CriterionResult`.

**Career-agent decides; Perplexity informs.** Nothing here asks a provider to judge a target: a
`ResearchPlan`'s query only ever asks for FACTS (a sector, a location, an activity), built from the
existing, unchanged deterministic evaluator's own output - never from the candidate's criteria
values (a required "Sector = Finance" criterion becomes "primary business activity and sector" in
the query, never "is this company in Finance?").

Only a criterion that can actually change the qualification is worth researching:

- `unknown ≠ false`: an `UNKNOWN` result is a genuine data gap, never a violation;
- only a `REQUIRED` criterion's outcome can flip `needs_information` to `candidate`/`excluded`
  (see `app.services.criteria_evaluation.decide_status`); a `preferred`/`flexible` gap is real
  and reported (`INFORMATIONAL`), but never triggers a mandatory search;
- `NOT_MATCHED` means data WAS available and simply did not match: it already answered the
  question (without excluding), so there is nothing left for research to add. Only `UNKNOWN`
  represents a gap research could fill;
- only dimensions the deterministic matcher searches as FREE TEXT are researchable at all
  (`sector`, `location` when it means the company's own, `keyword`/activity). A closed vocabulary
  (`country`, `contract_type`, `remote_mode`) needs one clean value, which this step deliberately
  never invents from prose (see `app.models.company_research`); a declared hard `constraint` is
  not a company-data question at all, and an offer-specific gap (`role`, `remote_mode`,
  offer-level `location`) is not something *company* research could answer.

Needs for the SAME COMPANY are grouped into ONE `ResearchPlan` (one query, several focus areas):
several targets often share a company, and several unknown dimensions of one company are answered
by the same research - this is where "500 opportunities" collapses to a handful of searches.
Unrelated companies are never merged into one query.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.integrations.research.ports import ResearchQuery, ResearchResult, ResearchSubject
from app.models import Company, CompanyResearchFact, Qualification, Target
from app.models.enums import CriterionDimension, CriterionLevel, CriterionOutcome
from app.repositories.targets import stage

MAX_RESULTS_PER_QUERY = 8

RESEARCH_OBJECTIVE = (
    "Determine publicly available information about the company's activity and technical "
    "environment relevant to the qualification criteria currently unresolved."
)

# Only dimensions the deterministic matcher searches as free text; see module docstring.
FIELD_LABEL: dict[CriterionDimension, str] = {
    CriterionDimension.SECTOR: "primary business activity and sector",
    CriterionDimension.LOCATION: "company location / headquarters",
    CriterionDimension.KEYWORD: "technology, products and relevant keywords",
}


class NeedPriority(StrEnum):
    """Whether resolving this need could change the qualification STATUS at all."""

    BLOCKING = "blocking"  # a required criterion: can flip needs_information -> candidate/excluded
    INFORMATIONAL = "informational"  # preferred/flexible: real gap, never gates the status


@dataclass(frozen=True)
class InformationNeed:
    """Why one stored `CriterionResult` is unresolved, and whether it is worth acting on."""

    target_id: int
    criterion_id: int
    dimension: CriterionDimension
    reason: str
    priority: NeedPriority


@dataclass(frozen=True)
class ResearchPlan:
    """One Perplexity call worth making: a company, and every need it would address."""

    company_id: int
    needs: tuple[InformationNeed, ...]
    query: ResearchQuery


def _researchable(dimension: CriterionDimension, *, has_offer: bool) -> bool:
    if dimension is CriterionDimension.LOCATION:
        return not has_offer  # the offer's own location is not a company-research question
    return dimension in FIELD_LABEL


def information_needs(target: Target, qualification: Qualification) -> list[InformationNeed]:
    """Deterministic: derived only from this qualification's own stored `CriterionResult`s."""
    has_offer = target.opportunity is not None
    needs: list[InformationNeed] = []
    for result in qualification.results:
        if result.outcome is not CriterionOutcome.UNKNOWN:
            continue  # NOT_MATCHED already answered the question; nothing to research
        criterion = result.criterion
        if not _researchable(criterion.dimension, has_offer=has_offer):
            continue
        priority = (
            NeedPriority.BLOCKING
            if criterion.level is CriterionLevel.REQUIRED
            else NeedPriority.INFORMATIONAL
        )
        needs.append(
            InformationNeed(
                target_id=target.id,
                criterion_id=criterion.id,
                dimension=criterion.dimension,
                reason=(
                    f"{criterion.dimension.value} is unknown for a "
                    f"{criterion.level.value} criterion"
                ),
                priority=priority,
            )
        )
    return needs


def plan_for_company(company: Company, needs: Sequence[InformationNeed]) -> ResearchPlan | None:
    """One query per company, covering every distinct researchable dimension in `needs`."""
    if not needs:
        return None
    dimensions = sorted({need.dimension for need in needs}, key=lambda d: d.value)
    focus_areas = tuple(FIELD_LABEL[dimension] for dimension in dimensions)
    query = ResearchQuery(
        objective=RESEARCH_OBJECTIVE,
        subject=ResearchSubject(
            company_name=company.name,
            website=company.website_url,
            location=company.location,
            sector=company.sector,
        ),
        focus_areas=focus_areas,
        max_results=MAX_RESULTS_PER_QUERY,
    )
    return ResearchPlan(company_id=company.id, needs=tuple(needs), query=query)


def accept_observations(
    session: Session, company_id: int, result: ResearchResult
) -> list[CompanyResearchFact]:
    """Store each SOURCED observation, verbatim, with its provenance. Never a rewrite of
    `Company`'s own fields, never a claim of verification - see `app.models.company_research`.

    An observation without a `source_url` is silently dropped, never stored: "no source, no
    fact" is the same rule already applied to contacts (`app.services.sources.
    require_channel_spec`). The current Perplexity adapter already guarantees a URL on every
    `Observation` it returns, but that guarantee is the adapter's own; this is the shared sink
    every `ResearchProvider` funnels through, so it is enforced here too, centrally, rather than
    trusted to each adapter.
    """
    return [
        stage(
            session,
            CompanyResearchFact(
                company_id=company_id,
                claim=observation.claim,
                source_url=observation.source_url,
                source_title=observation.source_title,
                excerpt=observation.excerpt,
                published=observation.published,
                retrieved_at=result.retrieved_at,
            ),
        )
        for observation in result.observations
        if observation.source_url
    ]
