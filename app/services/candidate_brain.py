"""Business rules of the Candidate Brain.

Rules enforced here (not in routes):
- there is a single main candidate;
- every fact, preference, constraint and evidence belongs to that candidate;
- an EvidenceLink can only join an evidence and a fact of the same candidate;
- nothing is ever inferred: a skill without evidence stays `unknown`, a skill level is only
  what the caller provided;
- absence is never a negation: a missing fact means "not known", and no rule here turns a
  missing row, missing evidence or a NULL field into a negative claim.
"""

from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Base,
    Candidate,
    CandidateConstraint,
    CandidatePreference,
    Certification,
    Education,
    Evidence,
    EvidenceLink,
    Experience,
    Language,
    Project,
    Skill,
)
from app.models.base import CandidateOwnedMixin
from app.models.enums import EvidenceTargetType
from app.models.evidence import LINK_COLUMNS
from app.repositories import candidate_brain as repo
from app.schemas.candidate import CandidateCreate
from app.schemas.evidence import EvidenceCreate, EvidenceLinkCreate
from app.schemas.facts import (
    CertificationCreate,
    EducationCreate,
    ExperienceCreate,
    LanguageCreate,
    ProjectCreate,
    SkillCreate,
)
from app.schemas.preferences import ConstraintCreate, PreferenceCreate

FACT_MODELS: dict[EvidenceTargetType, type[CandidateOwnedMixin]] = {
    EvidenceTargetType.SKILL: Skill,
    EvidenceTargetType.PROJECT: Project,
    EvidenceTargetType.EXPERIENCE: Experience,
    EvidenceTargetType.EDUCATION: Education,
    EvidenceTargetType.CERTIFICATION: Certification,
    EvidenceTargetType.LANGUAGE: Language,
}


class CandidateBrainService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- candidate -------------------------------------------------------------------

    def create_candidate(self, data: CandidateCreate) -> Candidate:
        if repo.get_first_candidate(self._session) is not None:
            raise ConflictError("A candidate already exists")
        return repo.add(self._session, Candidate(**data.model_dump()))

    def get_candidate(self) -> Candidate:
        candidate = repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate

    # --- facts -----------------------------------------------------------------------

    def add_education(self, data: EducationCreate) -> Education:
        return self._add(Education, data)

    def list_education(self) -> Sequence[Education]:
        return self._list(Education)

    def add_experience(self, data: ExperienceCreate) -> Experience:
        return self._add(Experience, data)

    def list_experiences(self) -> Sequence[Experience]:
        return self._list(Experience)

    def add_project(self, data: ProjectCreate) -> Project:
        return self._add(Project, data)

    def list_projects(self) -> Sequence[Project]:
        return self._list(Project)

    def add_skill(self, data: SkillCreate) -> Skill:
        return self._add(Skill, data)

    def list_skills(self) -> Sequence[Skill]:
        return self._list(Skill)

    def add_certification(self, data: CertificationCreate) -> Certification:
        return self._add(Certification, data)

    def list_certifications(self) -> Sequence[Certification]:
        return self._list(Certification)

    def add_language(self, data: LanguageCreate) -> Language:
        return self._add(Language, data)

    def list_languages(self) -> Sequence[Language]:
        return self._list(Language)

    # --- preferences & constraints (declarations, not facts) -------------------------

    def add_preferences(self, data: PreferenceCreate) -> CandidatePreference:
        candidate = self.get_candidate()
        if self._session.scalars(
            select(CandidatePreference).where(CandidatePreference.candidate_id == candidate.id)
        ).first():
            raise ConflictError("Preferences already exist for this candidate")
        values = data.model_dump()
        values["contract_types"] = [contract.value for contract in data.contract_types]
        return repo.add(self._session, CandidatePreference(candidate_id=candidate.id, **values))

    def get_preferences(self) -> CandidatePreference:
        candidate = self.get_candidate()
        preferences = self._session.scalars(
            select(CandidatePreference).where(CandidatePreference.candidate_id == candidate.id)
        ).first()
        if preferences is None:
            raise NotFoundError("No preferences have been defined yet")
        return preferences

    def add_constraint(self, data: ConstraintCreate) -> CandidateConstraint:
        return self._add(CandidateConstraint, data)

    def list_constraints(self) -> Sequence[CandidateConstraint]:
        return self._list(CandidateConstraint)

    # --- evidence --------------------------------------------------------------------

    def add_evidence(self, data: EvidenceCreate) -> Evidence:
        return self._add(Evidence, data)

    def list_evidence(self) -> Sequence[Evidence]:
        return self._list(Evidence)

    def link_evidence(self, data: EvidenceLinkCreate) -> EvidenceLink:
        """Declare that an evidence supports a fact. Both must belong to the candidate."""
        candidate = self.get_candidate()
        evidence = repo.get_for_candidate(self._session, Evidence, candidate.id, data.evidence_id)
        if evidence is None:
            raise NotFoundError(f"Evidence {data.evidence_id} not found")
        target = repo.get_for_candidate(
            self._session, FACT_MODELS[data.target_type], candidate.id, data.target_id
        )
        if target is None:
            raise NotFoundError(f"{data.target_type.value} {data.target_id} not found")
        link = EvidenceLink(
            evidence_id=evidence.id, note=data.note, **{LINK_COLUMNS[data.target_type]: target.id}
        )
        return repo.add(self._session, link)

    # --- helpers ---------------------------------------------------------------------

    def _add[T: Base](self, model: type[T], data: BaseModel) -> T:
        candidate = self.get_candidate()
        return repo.add(self._session, model(candidate_id=candidate.id, **data.model_dump()))

    def _list[T: CandidateOwnedMixin](self, model: type[T]) -> Sequence[T]:
        return repo.list_for_candidate(self._session, model, self.get_candidate().id)
