"""Concrete "Criteria v1" search profile (step 3a) - see docs/qualification_v1.md.

Criteria are DATA, not a new config file: this module seeds one named, versioned
`SearchProfile` (reusing `SearchProfileService`/`SearchCriterion`, unchanged) instead of
inventing a parallel criteria store. The profile is created once, lazily, on first use
(`ensure_profile`); its criteria are immutable (the existing `SearchCriterion` guarantee), so
"v1" never silently changes meaning - a future "v2" is a new profile, never an edit of this one.

Only ONE criterion is truly inflexible (`contract_type`): "alternance" (apprenticeship or
professionalization) is the only inflexible constraint (spec section 2.1). The job-family /
adjacent-technical vocabulary (sections 9-10) is expressed as a SECOND required criterion, but
on the `keyword` dimension, whose evaluator (`app.services.criteria_evaluation`) can only ever
return `satisfied` / `not_matched` / `unknown` for an `any_of` criterion - never `incompatible` -
so it can never, by itself, exclude a target (`decide_status`): an offer whose title/description
matches nothing recognisable becomes `needs_information`, never `excluded`. This is a POSITIVE
list (what the search IS about), not a blacklist (section 11 explicitly forbids those).

Every other dimension of the spec (location, availability, education, salary, skills, sector) is
deliberately NOT a criterion here: sections 3, 4, 5, 6, 7 and 8 all say so explicitly. Location is
instead shown, per qualification, as a non-gating `LocationTier` (`app.services.location_tier`);
the job family actually matched is shown as a non-gating `RoleFamily`
(`app.services.role_family`). Neither ever changes `QualificationStatus`.
"""

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import SearchProfile
from app.models.enums import CriterionDimension, CriterionLevel, CriterionOperator, EmploymentType
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as repo
from app.schemas.search import CriterionInput, SearchProfileCreate
from app.services.role_taxonomy import ROLE_TAXONOMY_VERSION, all_terms
from app.services.search_profiles import SearchProfileService

PROFILE_NAME = "Criteria v1"
CRITERIA_VERSION = "v1"

ALTERNANCE_CONTRACT_TYPES = [
    EmploymentType.APPRENTICESHIP.value,
    EmploymentType.PROFESSIONALIZATION.value,
]


def _definition() -> SearchProfileCreate:
    return SearchProfileCreate(
        name=PROFILE_NAME,
        description=(
            "Qualification Criteria v1 - deterministic scope: alternance (apprenticeship or "
            "professionalization) is the only inflexible constraint; the job-family/adjacent-"
            f"technical vocabulary (role taxonomy {ROLE_TAXONOMY_VERSION}) is required but can "
            "only ever move a target to needs_information, never exclude it."
        ),
        is_active=False,  # never silently replaces whatever profile the candidate already uses
        criteria=[
            CriterionInput(
                dimension=CriterionDimension.CONTRACT_TYPE,
                operator=CriterionOperator.ANY_OF,
                values=ALTERNANCE_CONTRACT_TYPES,
                level=CriterionLevel.REQUIRED,
                note="Alternance only (section 2.1): the sole inflexible constraint here.",
            ),
            CriterionInput(
                dimension=CriterionDimension.KEYWORD,
                operator=CriterionOperator.ANY_OF,
                values=all_terms(),
                level=CriterionLevel.REQUIRED,
                note=(
                    "Job-family + adjacent-technical vocabulary (sections 9-10, role taxonomy "
                    f"{ROLE_TAXONOMY_VERSION}). A miss is `not_matched`, never `incompatible`: it "
                    "can only raise a question, never exclude by itself."
                ),
            ),
        ],
    )


def _candidate_id(session: Session) -> int:
    candidate = brain_repo.get_first_candidate(session)
    if candidate is None:
        raise NotFoundError("No candidate has been created yet")
    return candidate.id


def ensure_profile(session: Session) -> SearchProfile:
    """The "Criteria v1" search profile, created once and reused afterwards.

    Idempotent by name: a second call finds the existing profile instead of creating a
    duplicate (criteria are immutable, so nothing here ever rewrites what "v1" means).
    """
    existing = next(
        (p for p in repo.list_profiles(session, _candidate_id(session)) if p.name == PROFILE_NAME),
        None,
    )
    if existing is not None:
        return existing
    return SearchProfileService(session).create_profile(_definition())
