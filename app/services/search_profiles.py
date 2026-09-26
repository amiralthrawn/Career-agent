"""Search profiles and criteria: creation, versioning (immutability) and explicit seeding.

Criteria are immutable. "Modifying" one creates a new criterion (`replaces`) and deactivates the
old one, which keeps a pointer to its successor: the history stays readable and past
qualifications keep their meaning. "Deleting" a criterion only deactivates it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.models import CandidateConstraint, CandidatePreference, SearchCriterion, SearchProfile
from app.models.enums import CriterionOrigin, ProfileOrigin
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as repo
from app.repositories.targets import stage
from app.schemas.search import CriterionInput, FromPreferencesRequest, SearchProfileCreate
from app.services.profile_seeding import seed_criteria


class SearchProfileService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- profiles ---------------------------------------------------------------------

    def list_profiles(self) -> Sequence[SearchProfile]:
        return repo.list_profiles(self._session, self._candidate_id())

    def get_profile(self, profile_id: int) -> SearchProfile:
        profile = repo.get_profile(self._session, self._candidate_id(), profile_id)
        if profile is None:
            raise NotFoundError(f"Search profile {profile_id} not found")
        return profile

    def active_profile(self) -> SearchProfile:
        profile = repo.active_profile(self._session, self._candidate_id())
        if profile is None:
            raise NotFoundError("No active search profile")
        return profile

    def create_profile(self, data: SearchProfileCreate) -> SearchProfile:
        profile = self._create(
            data.name,
            data.description,
            data.is_active,
            ProfileOrigin.MANUAL,
            unmapped=[],
        )
        for criterion in data.criteria:
            self._add_criterion(profile, criterion, CriterionOrigin.MANUAL, None)
        return self._finish(profile)

    def _create(
        self,
        name: str,
        description: str | None,
        is_active: bool | None,
        origin: ProfileOrigin,
        *,
        unmapped: list[dict[str, str]],
    ) -> SearchProfile:
        candidate_id = self._candidate_id()
        if any(p.name == name for p in repo.list_profiles(self._session, candidate_id)):
            raise ConflictError("A search profile with this name already exists")
        has_active = repo.active_profile(self._session, candidate_id) is not None
        activate = (not has_active) if is_active is None else is_active
        if activate:
            repo.deactivate_profiles(self._session, candidate_id)
        profile = SearchProfile(
            candidate_id=candidate_id,
            name=name,
            description=description,
            is_active=activate,
            origin=origin,
            unmapped=unmapped,
        )
        return stage(self._session, profile)

    def _finish(self, profile: SearchProfile) -> SearchProfile:
        self._session.flush()
        self._session.expire(profile, ["criteria"])
        self._session.commit()
        return profile

    # --- criteria ---------------------------------------------------------------------

    def add_criterion(self, profile_id: int, data: CriterionInput) -> SearchProfile:
        profile = self.get_profile(profile_id)
        self._add_criterion(profile, data, CriterionOrigin.MANUAL, None)
        return self._finish(profile)

    def _add_criterion(
        self,
        profile: SearchProfile,
        data: CriterionInput,
        origin: CriterionOrigin,
        origin_ref: str | None,
    ) -> SearchCriterion:
        previous: SearchCriterion | None = None
        if data.replaces is not None:
            previous = repo.get_criterion(self._session, profile.id, data.replaces)
            if previous is None:
                raise NotFoundError(f"Criterion {data.replaces} not found in this profile")
            if not previous.active:
                raise UnprocessableError("Only an active criterion can be replaced")
        criterion = SearchCriterion(
            profile_id=profile.id,
            dimension=data.dimension,
            operator=data.operator,
            match_values=list(data.values),
            level=data.level,
            note=data.note,
            origin=origin,
            origin_ref=origin_ref,
        )
        stage(self._session, criterion)
        if previous is not None:  # the only allowed change to an existing criterion
            previous.active = False
            previous.deactivated_at = datetime.now(UTC)
            previous.superseded_by_id = criterion.id
            self._session.flush()
        return criterion

    def deactivate_criterion(self, profile_id: int, criterion_id: int) -> SearchCriterion:
        profile = self.get_profile(profile_id)
        criterion = repo.get_criterion(self._session, profile.id, criterion_id)
        if criterion is None:
            raise NotFoundError(f"Criterion {criterion_id} not found in this profile")
        if criterion.active:
            criterion.active = False
            criterion.deactivated_at = datetime.now(UTC)
            self._session.flush()
        self._session.expire(profile, ["criteria"])
        self._session.commit()
        return criterion

    # --- seeding ----------------------------------------------------------------------

    def from_preferences(self, data: FromPreferencesRequest) -> SearchProfile:
        candidate_id = self._candidate_id()
        preference = self._session.scalars(
            select(CandidatePreference).where(CandidatePreference.candidate_id == candidate_id)
        ).first()
        constraints = list(
            self._session.scalars(
                select(CandidateConstraint)
                .where(CandidateConstraint.candidate_id == candidate_id)
                .order_by(CandidateConstraint.id)
            )
        )
        if preference is None and not constraints:
            raise UnprocessableError(
                "There are no preferences or constraints to build a profile from"
            )
        seed = seed_criteria(preference, constraints, data.levels)
        profile = self._create(
            data.name, None, data.is_active, ProfileOrigin.FROM_PREFERENCES, unmapped=seed.unmapped
        )
        for draft in seed.drafts:
            self._add_criterion(profile, draft.data, draft.origin, draft.origin_ref)
        return self._finish(profile)
