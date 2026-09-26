"""Model-level tests: persistence, relations, evidence traceability."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Candidate,
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
from app.models.enums import Confidence, EvidenceTargetType, InformationState, SourceType
from app.models.evidence import derive_information_state


@pytest.fixture
def candidate(db_session: Session) -> Candidate:
    instance = Candidate(first_name="Test", last_name="Candidate-Fixture")
    db_session.add(instance)
    db_session.commit()
    return instance


def make_evidence(
    candidate: Candidate,
    *,
    confidence: Confidence = Confidence.LOW,
    verified: bool = False,
    name: str = "fixture document",
) -> Evidence:
    return Evidence(
        candidate_id=candidate.id,
        source_type=SourceType.DOCUMENT,
        source_name=name,
        confidence=confidence,
        verified=verified,
    )


def link(evidence: Evidence, **target: int) -> EvidenceLink:
    return EvidenceLink(evidence=evidence, **target)


# --- Candidate -----------------------------------------------------------------------


def test_candidate_is_created_and_retrieved(db_session: Session) -> None:
    db_session.add(Candidate(first_name="Test", last_name="Candidate-Fixture", headline="h"))
    db_session.commit()

    stored = db_session.scalars(select(Candidate)).one()

    assert stored.id is not None
    assert stored.headline == "h"
    assert stored.created_at is not None and stored.updated_at is not None


# --- Facts: creation + relation to candidate -----------------------------------------


def test_education_belongs_to_candidate(db_session: Session, candidate: Candidate) -> None:
    db_session.add(Education(candidate_id=candidate.id, institution="Fixture Institute"))
    db_session.commit()

    db_session.refresh(candidate)
    assert [e.institution for e in candidate.education] == ["Fixture Institute"]


def test_experience_belongs_to_candidate(db_session: Session, candidate: Candidate) -> None:
    db_session.add(Experience(candidate_id=candidate.id, company="Fixture Co", title="Role"))
    db_session.commit()

    db_session.refresh(candidate)
    assert [e.company for e in candidate.experiences] == ["Fixture Co"]


def test_project_belongs_to_candidate(db_session: Session, candidate: Candidate) -> None:
    db_session.add(Project(candidate_id=candidate.id, name="Fixture Project"))
    db_session.commit()

    db_session.refresh(candidate)
    assert [p.name for p in candidate.projects] == ["Fixture Project"]


def test_skill_belongs_to_candidate_and_level_is_not_invented(
    db_session: Session, candidate: Candidate
) -> None:
    db_session.add(Skill(candidate_id=candidate.id, name="Fixture Skill"))
    db_session.commit()

    db_session.refresh(candidate)
    skill = candidate.skills[0]
    assert skill.name == "Fixture Skill"
    assert skill.level is None
    assert skill.state is InformationState.UNKNOWN


def test_skill_names_are_unique_per_candidate(db_session: Session, candidate: Candidate) -> None:
    db_session.add(Skill(candidate_id=candidate.id, name="X"))
    db_session.commit()
    db_session.add(Skill(candidate_id=candidate.id, name="X"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_certification_and_language_belong_to_candidate(
    db_session: Session, candidate: Candidate
) -> None:
    db_session.add_all(
        [
            Certification(candidate_id=candidate.id, name="Fixture Cert"),
            Language(candidate_id=candidate.id, language="Fixture Language"),
        ]
    )
    db_session.commit()

    db_session.refresh(candidate)
    assert len(candidate.certifications) == 1
    assert candidate.languages[0].level is None


# --- Preferences are not facts -------------------------------------------------------


def test_preference_is_stored_separately_from_skills(
    db_session: Session, candidate: Candidate
) -> None:
    db_session.add(
        CandidatePreference(
            candidate_id=candidate.id, target_domains=["Data/IA"], target_roles=["Role"]
        )
    )
    db_session.commit()

    db_session.refresh(candidate)
    assert candidate.preferences is not None
    assert candidate.preferences.target_domains == ["Data/IA"]
    # A stated preference never creates a skill, and cannot carry evidence.
    assert db_session.scalars(select(Skill)).all() == []
    assert not hasattr(CandidatePreference, "evidence_links")


def test_only_one_preference_row_per_candidate(db_session: Session, candidate: Candidate) -> None:
    db_session.add(CandidatePreference(candidate_id=candidate.id))
    db_session.commit()
    db_session.add(CandidatePreference(candidate_id=candidate.id))

    with pytest.raises(IntegrityError):
        db_session.commit()


# --- Evidence ------------------------------------------------------------------------


def test_evidence_defaults_are_conservative(db_session: Session, candidate: Candidate) -> None:
    evidence = Evidence(
        candidate_id=candidate.id, source_type=SourceType.CV, source_name="fixture cv"
    )
    db_session.add(evidence)
    db_session.commit()

    assert evidence.verified is False
    assert evidence.confidence is Confidence.LOW
    assert evidence.source_metadata == {}
    assert evidence.source_uri is None


def test_evidence_supports_each_kind_of_fact(db_session: Session, candidate: Candidate) -> None:
    facts: dict[str, Any] = {
        "skill_id": Skill(candidate_id=candidate.id, name="s"),
        "project_id": Project(candidate_id=candidate.id, name="p"),
        "experience_id": Experience(candidate_id=candidate.id, company="c", title="t"),
        "education_id": Education(candidate_id=candidate.id, institution="i"),
        "certification_id": Certification(candidate_id=candidate.id, name="c"),
        "language_id": Language(candidate_id=candidate.id, language="l"),
    }
    db_session.add_all(facts.values())
    evidence = make_evidence(candidate)
    db_session.add(evidence)
    db_session.commit()

    for column, fact in facts.items():
        db_session.add(link(evidence, **{column: fact.id}))
    db_session.commit()

    assert {item.target_type for item in evidence.links} == set(EvidenceTargetType)
    for fact in facts.values():
        assert fact.evidence_ids == [evidence.id]


def test_link_requires_exactly_one_target(db_session: Session, candidate: Candidate) -> None:
    skill = Skill(candidate_id=candidate.id, name="s")
    project = Project(candidate_id=candidate.id, name="p")
    evidence = make_evidence(candidate)
    db_session.add_all([skill, project, evidence])
    db_session.commit()

    db_session.add(link(evidence, skill_id=skill.id, project_id=project.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(link(evidence))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_evidence_cannot_be_linked_twice_to_same_fact(
    db_session: Session, candidate: Candidate
) -> None:
    skill = Skill(candidate_id=candidate.id, name="s")
    evidence = make_evidence(candidate)
    db_session.add_all([skill, evidence])
    db_session.commit()
    db_session.add_all([link(evidence, skill_id=skill.id), link(evidence, skill_id=skill.id)])

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_skill_can_be_traced_back_to_its_evidence(
    db_session: Session, candidate: Candidate
) -> None:
    """'Why does the agent claim this skill?' -> follow the links to the evidence."""
    skill = Skill(candidate_id=candidate.id, name="Fixture Skill")
    project = Project(candidate_id=candidate.id, name="Fixture Project")
    project_evidence = make_evidence(candidate, name="project repo", confidence=Confidence.HIGH)
    cv_evidence = make_evidence(candidate, name="cv", verified=True)
    db_session.add_all([skill, project, project_evidence, cv_evidence])
    db_session.commit()
    db_session.add_all(
        [
            link(project_evidence, skill_id=skill.id),
            link(cv_evidence, skill_id=skill.id),
            link(project_evidence, project_id=project.id),
        ]
    )
    db_session.commit()
    db_session.refresh(skill)

    assert {item.evidence.source_name for item in skill.evidence_links} == {"project repo", "cv"}
    assert skill.evidence_ids == sorted([project_evidence.id, cv_evidence.id])
    assert skill.state is InformationState.VERIFIED
    assert project.state is InformationState.KNOWN


@pytest.mark.parametrize(
    ("evidence_specs", "expected"),
    [
        ([], InformationState.UNKNOWN),
        ([(Confidence.LOW, False)], InformationState.UNCERTAIN),
        ([(Confidence.MEDIUM, False)], InformationState.KNOWN),
        ([(Confidence.HIGH, False), (Confidence.LOW, False)], InformationState.KNOWN),
        ([(Confidence.LOW, True)], InformationState.VERIFIED),
    ],
)
def test_information_state_is_derived_from_evidence(
    candidate: Candidate,
    evidence_specs: list[tuple[Confidence, bool]],
    expected: InformationState,
) -> None:
    evidence = [
        make_evidence(candidate, confidence=confidence, verified=verified)
        for confidence, verified in evidence_specs
    ]

    assert derive_information_state(evidence) is expected


def test_deleting_evidence_returns_fact_to_unknown(
    db_session: Session, candidate: Candidate
) -> None:
    skill = Skill(candidate_id=candidate.id, name="s")
    evidence = make_evidence(candidate, verified=True)
    db_session.add_all([skill, evidence])
    db_session.commit()
    db_session.add(link(evidence, skill_id=skill.id))
    db_session.commit()
    db_session.refresh(skill)
    assert skill.evidence_ids == [evidence.id]

    db_session.delete(evidence)
    db_session.commit()
    db_session.refresh(skill)

    assert skill.state is InformationState.UNKNOWN


def test_deleting_candidate_removes_everything(db_session: Session, candidate: Candidate) -> None:
    skill = Skill(candidate_id=candidate.id, name="s")
    evidence = make_evidence(candidate)
    db_session.add_all([skill, evidence])
    db_session.commit()
    db_session.add(link(evidence, skill_id=skill.id))
    db_session.commit()

    db_session.delete(candidate)
    db_session.commit()

    for model in (Skill, Evidence, EvidenceLink):
        assert db_session.scalars(select(model)).all() == []
