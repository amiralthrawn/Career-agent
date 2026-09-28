"""Facade over the EXISTING `QualificationService`, for "Criteria v1" (step 3a).

`CLI -> OpportunityQualificationService -> QualificationService/repositories -> SQLite`
(the same architecture the spec asks for): this module adds NO new decision logic, no LLM, no
network call, no write to the Candidate Brain. It only (a) makes sure the "Criteria v1" profile
exists (`app.services.criteria_v1`), (b) calls the unchanged `QualificationService` with that
profile's id, and (c) labels the result for a human (`app.schemas.opportunity_qualification`,
`app.services.role_family`, `app.services.location_tier`) - all three purely at read time.

Both the CLI (`app.cli.qualification`) and the API (`app.api.opportunity_qualification`) call
this SAME service, so the business logic is never duplicated between them (spec section 20).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import Qualification, Target
from app.models.enums import QualificationStatus
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as repo
from app.repositories import targets as target_repo
from app.schemas.opportunity_qualification import (
    DECISION_LABELS,
    V1OpportunitySummary,
    V1QualificationRead,
    to_v1_reasons,
)
from app.schemas.qualification import read_qualification
from app.services import criteria_v1, location_tier, role_family
from app.services.qualification import QualificationService


@dataclass
class RunOutcome:
    qualification: V1QualificationRead
    created: bool


class OpportunityQualificationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._qualification = QualificationService(session)

    # --- profile -------------------------------------------------------------------------

    def _profile_id(self) -> int:
        return criteria_v1.ensure_profile(self._session).id

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- present ---------------------------------------------------------------------------

    def _present(
        self, target: Target, qualification: Qualification, *, stale: bool
    ) -> V1QualificationRead:
        read = read_qualification(qualification, stale=stale)
        role = role_family.classify(target)
        location = location_tier.classify(target)
        return V1QualificationRead(
            target_id=target.id,
            criteria_version=criteria_v1.CRITERIA_VERSION,
            decision=DECISION_LABELS[qualification.status],
            stale=stale,
            created_at=qualification.created_at,
            role_family=role.family,
            role_family_matched_term=role.matched_term,
            location_tier=location.tier,
            reasons=to_v1_reasons(read),
        )

    # --- run / show ------------------------------------------------------------------------

    def run(self, target_id: int) -> RunOutcome:
        """Qualify a target against "Criteria v1". Idempotent: unchanged inputs return the
        existing qualification (`created=False`), exactly like the underlying service."""
        outcome = self._qualification.qualify(target_id, self._profile_id())
        target = self._qualification.target(target_id)
        return RunOutcome(
            self._present(target, outcome.qualification, stale=False), outcome.created
        )

    def show(self, target_id: int) -> V1QualificationRead:
        current = self._qualification.current(target_id, self._profile_id())
        target = self._qualification.target(target_id)
        return self._present(target, current.qualification, stale=current.stale)

    # --- list --------------------------------------------------------------------------------

    def _summary(self, target: Target, qualification: Qualification) -> V1OpportunitySummary:
        return V1OpportunitySummary(
            target_id=target.id,
            company_name=target.company.name,
            offer_title=target.opportunity.title if target.opportunity else None,
            decision=DECISION_LABELS[qualification.status],
            role_family=role_family.classify(target).family,
            location_tier=location_tier.classify(target).tier,
            created_at=qualification.created_at,
        )

    def _list_by_status(self, status: QualificationStatus) -> Sequence[V1OpportunitySummary]:
        candidate_id = self._candidate_id()
        profile_id = self._profile_id()
        rows = repo.latest_for_profile(self._session, candidate_id, profile_id)
        summaries = []
        for qualification in rows:
            if qualification.status is not status:
                continue
            target = target_repo.get_target(self._session, candidate_id, qualification.target_id)
            if target is not None:  # defensive: a target is never deleted, but never assume
                summaries.append(self._summary(target, qualification))
        return summaries

    def list_qualified(self) -> Sequence[V1OpportunitySummary]:
        return self._list_by_status(QualificationStatus.CANDIDATE)

    def list_uncertain(self) -> Sequence[V1OpportunitySummary]:
        return self._list_by_status(QualificationStatus.NEEDS_INFORMATION)

    def list_excluded(self) -> Sequence[V1OpportunitySummary]:
        return self._list_by_status(QualificationStatus.EXCLUDED)
