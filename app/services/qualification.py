"""Qualification of targets against a search profile (deterministic, step 3a).

- A qualification is IMMUTABLE and stores the fingerprint of its inputs.
- Same fingerprint as the latest qualification -> nothing is recomputed (idempotent).
- Inputs changed (criteria, target/company/offer data, evaluator version) -> the previous
  qualification is reported stale and a NEW one can be computed; the old one stays as history.
- Excluded targets are kept, with their reasons: nothing is deleted or dismissed automatically
  (`Target.status` remains the human's decision).
- No score, no percentage: a status, one result per criterion, and reasons as codes + references.
- Step 3b: the same qualification also records, for each ACTIVE requirement of the target, what
  the Candidate Brain establishes (`RequirementMatch`). Requirements and the Brain snapshot are
  part of the fingerprint. Matches NEVER change the status: a skill gap does not exclude a target.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.models import (
    CriterionResult,
    Qualification,
    QualificationReason,
    RequirementMatch,
    RequirementMatchFact,
    SearchProfile,
    Target,
)
from app.models.audit import AuditEventType
from app.models.enums import EvidenceTargetType, QualificationMethod, QualificationStatus
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as repo
from app.repositories import targets as target_repo
from app.schemas.qualification import RunItem, RunReport
from app.services.audit import AuditLog
from app.services.criteria_evaluation import (
    CriterionSpec,
    TargetView,
    build_reasons,
    build_view,
    decide_status,
    evaluate,
    fingerprint,
)
from app.services.requirement_matching import MatchingInputs
from app.services.requirements import RequirementService
from app.services.search_profiles import SearchProfileService


@dataclass
class QualifyOutcome:
    qualification: Qualification
    created: bool


@dataclass
class CurrentQualification:
    qualification: Qualification
    stale: bool


def _specs(profile: SearchProfile) -> list[CriterionSpec]:
    return [
        CriterionSpec(
            id=criterion.id,
            dimension=criterion.dimension,
            operator=criterion.operator,
            values=tuple(criterion.match_values),
            level=criterion.level,
        )
        for criterion in profile.criteria
        if criterion.active
    ]


class QualificationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._profiles = SearchProfileService(session)
        self._requirements = RequirementService(session)

    # --- helpers ----------------------------------------------------------------------

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    def target(self, target_id: int) -> Target:
        return self._target(target_id)

    def _profile(self, profile_id: int | None) -> SearchProfile:
        if profile_id is None:
            return self._profiles.active_profile()
        return self._profiles.get_profile(profile_id)

    def _target(self, target_id: int) -> Target:
        target = target_repo.get_target(self._session, self._candidate_id(), target_id)
        if target is None:
            raise NotFoundError(f"Target {target_id} not found")
        return target

    def _inputs(
        self, profile: SearchProfile, target: Target
    ) -> tuple[list[CriterionSpec], TargetView, MatchingInputs, str]:
        specs = _specs(profile)
        if not specs:
            raise UnprocessableError("The search profile has no active criteria")
        view = build_view(target)
        matching = self._requirements.matching_inputs(target)
        return specs, view, matching, fingerprint(profile.id, specs, view, matching)

    # --- qualify ----------------------------------------------------------------------

    def qualify(
        self, target_id: int, profile_id: int | None = None, *, commit: bool = True
    ) -> QualifyOutcome:
        return self._qualify(self._target(target_id), self._profile(profile_id), commit=commit)

    def _qualify(self, target: Target, profile: SearchProfile, *, commit: bool) -> QualifyOutcome:
        specs, view, matching, digest = self._inputs(profile, target)
        latest = repo.latest_qualification(self._session, target.id, profile.id)
        if latest is not None and latest.inputs_fingerprint == digest:
            return QualifyOutcome(latest, False)  # same inputs: nothing to recompute

        evaluations = [evaluate(spec, view) for spec in specs]
        pairs = [(spec.level, e.outcome) for spec, e in zip(specs, evaluations, strict=True)]
        status = decide_status(pairs)
        qualification = target_repo.stage(
            self._session,
            Qualification(
                candidate_id=target.candidate_id,
                target_id=target.id,
                profile_id=profile.id,
                method=QualificationMethod.DETERMINISTIC,
                inputs_fingerprint=digest,
                status=status,
            ),
        )
        results = [
            target_repo.stage(
                self._session,
                CriterionResult(
                    qualification_id=qualification.id,
                    criterion_id=spec.id,
                    outcome=evaluation.outcome,
                    code=evaluation.code,
                    observed=evaluation.observed,
                ),
            )
            for spec, evaluation in zip(specs, evaluations, strict=True)
        ]
        reasons = build_reasons(pairs, has_offer=view.has_offer)
        for position, reason in enumerate(reasons):
            target_repo.stage(
                self._session,
                QualificationReason(
                    qualification_id=qualification.id,
                    position=position,
                    code=reason.code,
                    criterion_result_id=(
                        results[reason.result_index].id if reason.result_index is not None else None
                    ),
                    origin=QualificationMethod.DETERMINISTIC,
                ),
            )
        self._record_matches(qualification, matching)
        self._session.flush()
        self._session.expire(qualification)
        if commit:
            self._session.commit()
        return QualifyOutcome(qualification, True)

    def _record_matches(self, qualification: Qualification, matching: MatchingInputs) -> None:
        """One immutable match per active requirement, with the Brain facts it rests on."""
        columns = {
            EvidenceTargetType.SKILL: "skill_id",
            EvidenceTargetType.PROJECT: "project_id",
            EvidenceTargetType.EXPERIENCE: "experience_id",
        }
        for result in self._requirements.matcher.match_all(
            list(matching.requirements), matching.brain
        ):
            match = target_repo.stage(
                self._session,
                RequirementMatch(
                    qualification_id=qualification.id,
                    requirement_id=result.requirement_id,
                    status=result.status,
                    note=result.note,
                ),
            )
            for fact in result.facts:
                target_repo.stage(
                    self._session,
                    RequirementMatchFact(
                        match_id=match.id,
                        role=fact.role,
                        state=fact.state,
                        **{columns[fact.type]: fact.id},
                    ),
                )

    # --- read -------------------------------------------------------------------------

    def current(self, target_id: int, profile_id: int | None = None) -> CurrentQualification:
        target = self._target(target_id)
        profile = self._profile(profile_id)
        latest = repo.latest_qualification(self._session, target.id, profile.id)
        if latest is None:
            raise NotFoundError("This target has not been qualified against this profile")
        stale = True
        specs = _specs(profile)
        if specs:
            matching = self._requirements.matching_inputs(target)
            current = fingerprint(profile.id, specs, build_view(target), matching)
            stale = current != latest.inputs_fingerprint
        return CurrentQualification(latest, stale)

    # --- batch ------------------------------------------------------------------------

    def run(self, profile_id: int | None = None, *, actor: str = "api") -> RunReport:
        """Qualify every non-dismissed target. Unchanged targets are left alone (idempotent).

        The audit event (counters only) is committed together with the new qualifications.
        """
        profile = self._profile(profile_id)
        if not _specs(profile):
            raise UnprocessableError("The search profile has no active criteria")
        items: list[RunItem] = []
        by_status: dict[QualificationStatus, int] = {status: 0 for status in QualificationStatus}
        for target in repo.targets_to_qualify(self._session, self._candidate_id()):
            outcome = self._qualify(target, profile, commit=False)
            by_status[outcome.qualification.status] += 1
            items.append(
                RunItem(
                    target_id=target.id,
                    status=outcome.qualification.status,
                    created=outcome.created,
                )
            )
        created = sum(1 for item in items if item.created)
        AuditLog(self._session).record(
            AuditEventType.QUALIFICATION_RUN,
            actor=actor,
            details={"rows": len(items), "created": created, "matched": len(items) - created},
        )
        return RunReport(
            profile_id=profile.id,
            targets_processed=len(items),
            created=created,
            unchanged=len(items) - created,
            by_status=by_status,
            items=items,
        )
