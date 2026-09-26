"""Candidate Brain routes: thin HTTP layer, all rules live in `CandidateBrainService`."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
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
from app.schemas.candidate import CandidateCreate, CandidateRead
from app.schemas.evidence import (
    EvidenceCreate,
    EvidenceLinkCreate,
    EvidenceLinkRead,
    EvidenceRead,
)
from app.schemas.facts import (
    CertificationCreate,
    CertificationRead,
    EducationCreate,
    EducationRead,
    ExperienceCreate,
    ExperienceRead,
    LanguageCreate,
    LanguageRead,
    ProjectCreate,
    ProjectRead,
    SkillCreate,
    SkillRead,
)
from app.schemas.preferences import (
    ConstraintCreate,
    ConstraintRead,
    PreferenceCreate,
    PreferenceRead,
)
from app.services.candidate_brain import CandidateBrainService


def get_service(session: Annotated[Session, Depends(get_db)]) -> CandidateBrainService:
    return CandidateBrainService(session)


Service = Annotated[CandidateBrainService, Depends(get_service)]

router = APIRouter(prefix="/api/candidate", tags=["candidate"])


@router.get("", response_model=CandidateRead)
def get_candidate(service: Service) -> Candidate:
    return service.get_candidate()


@router.post("", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
def create_candidate(data: CandidateCreate, service: Service) -> Candidate:
    return service.create_candidate(data)


@router.get("/skills", response_model=list[SkillRead])
def list_skills(service: Service) -> Sequence[Skill]:
    return service.list_skills()


@router.post("/skills", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
def create_skill(data: SkillCreate, service: Service) -> Skill:
    return service.add_skill(data)


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(service: Service) -> Sequence[Project]:
    return service.list_projects()


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(data: ProjectCreate, service: Service) -> Project:
    return service.add_project(data)


@router.get("/experiences", response_model=list[ExperienceRead])
def list_experiences(service: Service) -> Sequence[Experience]:
    return service.list_experiences()


@router.post("/experiences", response_model=ExperienceRead, status_code=status.HTTP_201_CREATED)
def create_experience(data: ExperienceCreate, service: Service) -> Experience:
    return service.add_experience(data)


@router.get("/education", response_model=list[EducationRead])
def list_education(service: Service) -> Sequence[Education]:
    return service.list_education()


@router.post("/education", response_model=EducationRead, status_code=status.HTTP_201_CREATED)
def create_education(data: EducationCreate, service: Service) -> Education:
    return service.add_education(data)


@router.get("/certifications", response_model=list[CertificationRead])
def list_certifications(service: Service) -> Sequence[Certification]:
    return service.list_certifications()


@router.post(
    "/certifications", response_model=CertificationRead, status_code=status.HTTP_201_CREATED
)
def create_certification(data: CertificationCreate, service: Service) -> Certification:
    return service.add_certification(data)


@router.get("/languages", response_model=list[LanguageRead])
def list_languages(service: Service) -> Sequence[Language]:
    return service.list_languages()


@router.post("/languages", response_model=LanguageRead, status_code=status.HTTP_201_CREATED)
def create_language(data: LanguageCreate, service: Service) -> Language:
    return service.add_language(data)


@router.get("/preferences", response_model=PreferenceRead)
def get_preferences(service: Service) -> CandidatePreference:
    return service.get_preferences()


@router.post("/preferences", response_model=PreferenceRead, status_code=status.HTTP_201_CREATED)
def create_preferences(data: PreferenceCreate, service: Service) -> CandidatePreference:
    return service.add_preferences(data)


@router.get("/constraints", response_model=list[ConstraintRead])
def list_constraints(service: Service) -> Sequence[CandidateConstraint]:
    return service.list_constraints()


@router.post("/constraints", response_model=ConstraintRead, status_code=status.HTTP_201_CREATED)
def create_constraint(data: ConstraintCreate, service: Service) -> CandidateConstraint:
    return service.add_constraint(data)


@router.get("/evidence", response_model=list[EvidenceRead])
def list_evidence(service: Service) -> Sequence[Evidence]:
    return service.list_evidence()


@router.post("/evidence", response_model=EvidenceRead, status_code=status.HTTP_201_CREATED)
def create_evidence(data: EvidenceCreate, service: Service) -> Evidence:
    return service.add_evidence(data)


@router.post(
    "/evidence-links", response_model=EvidenceLinkRead, status_code=status.HTTP_201_CREATED
)
def link_evidence(data: EvidenceLinkCreate, service: Service) -> EvidenceLink:
    return service.link_evidence(data)
