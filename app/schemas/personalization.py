"""The PersonalizationBrief: the ONLY thing a future writer (LLM or human) is allowed to read.

It is computed on demand from the latest qualification (which stays the source of truth) and is
not persisted. It contains no score and no timestamp, so the same inputs give the same brief.
Every item carries the ids of the system objects it comes from (requirement, Skill / Project /
Experience, qualification).

CONTRACT: a brief on a stale qualification is returned with `qualification.stale = true`
(not refused). The future personalisation module must check that field before generating.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import (
    EvidenceTargetType,
    InformationState,
    MatchFactRole,
    MatchNote,
    MatchStatus,
    QualificationStatus,
    RequirementImportance,
    RequirementKind,
    RequirementOrigin,
    SourceKind,
)

Mode = Literal["offer", "spontaneous"]

QuestionCode = Literal[
    "confirm_skill_evidence",
    "confirm_project_skill",
    "confirm_experience_evidence",
    "provide_experience_dates",
    "relate_experience_to_scope",
]

QUESTION_BY_NOTE: dict[MatchNote, QuestionCode] = {
    MatchNote.SKILL_UNCONFIRMED: "confirm_skill_evidence",
    MatchNote.MENTIONED_BY_PROJECT_ONLY: "confirm_project_skill",
    MatchNote.EXPERIENCE_UNCONFIRMED: "confirm_experience_evidence",
    MatchNote.EXPERIENCE_DATES_INSUFFICIENT: "provide_experience_dates",
    MatchNote.EXPERIENCE_SCOPE_NOT_MEASURABLE: "relate_experience_to_scope",
}

QUESTION_TEXT: dict[QuestionCode, str] = {
    "confirm_skill_evidence": "Is there evidence that confirms the skill '{label}'?",
    "confirm_project_skill": (
        "A project or experience mentions '{label}': should it be recorded as a Skill, with "
        "evidence?"
    ),
    "confirm_experience_evidence": (
        "Can the experiences that would reach '{label}' be backed by evidence?"
    ),
    "provide_experience_dates": (
        "Which precise dates do the experiences have, to compare them with '{label}'?"
    ),
    "relate_experience_to_scope": (
        "Which of the candidate's experiences relate to the domain of '{label}'?"
    ),
}


class BriefSource(BaseModel):
    kind: SourceKind
    label: str
    url: str | None
    reference: str | None


class BriefCompany(BaseModel):
    id: int
    name: str


class BriefOffer(BaseModel):
    id: int
    title: str
    url: str | None


class BriefTarget(BaseModel):
    target_id: int
    mode: Mode
    company: BriefCompany
    offer: BriefOffer | None  # None: spontaneous application
    contract_type: str | None
    source: BriefSource  # where the target itself comes from


class BriefQualification(BaseModel):
    id: int
    profile_id: int
    status: QualificationStatus
    stale: bool = Field(
        description=(
            "True when the criteria, the target, the requirements or the Candidate Brain changed "
            "since this qualification. The brief is still returned, but a personalisation "
            "module MUST NOT generate content from it: re-qualify the target and read the "
            "brief again."
        )
    )
    inputs_fingerprint: str


class RequirementRef(BaseModel):
    id: int
    kind: RequirementKind
    key: str
    label: str
    importance: RequirementImportance
    origin: RequirementOrigin
    excerpt: str  # exact text of the source


class BriefFact(BaseModel):
    type: EvidenceTargetType
    id: int
    name: str
    state: InformationState
    role: MatchFactRole


class Strength(BaseModel):
    """A requirement the Brain truly establishes, with the facts it rests on."""

    requirement: RequirementRef
    facts: list[BriefFact]


class DoNotClaim(BaseModel):
    """Something the Brain does NOT establish. Nothing in the message may assert it."""

    requirement: RequirementRef
    status: Literal[MatchStatus.GAP, MatchStatus.WEAK, MatchStatus.UNMEASURABLE]
    note: MatchNote
    text: str
    facts: list[BriefFact]


class OpenQuestion(BaseModel):
    """Something only the human can settle before it may be used."""

    question: QuestionCode
    requirement: RequirementRef
    facts: list[BriefFact]
    text: str


class EmphasisFact(BaseModel):
    type: EvidenceTargetType
    id: int
    name: str
    state: InformationState


class EmphasisCandidate(BaseModel):
    """A fact worth putting forward. Ranked by how many covered requirements it supports."""

    rank: int  # position in the list (1 = first), not a score
    fact: EmphasisFact
    covered_requirement_ids: list[int]
    covered_count: int


class CompanyContext(BaseModel):
    """Structure only, from data already stored. No external information, possibly empty."""

    sector: str | None = None
    location: str | None = None
    country_code: str | None = None
    domain: str | None = None
    website_url: str | None = None
    careers_url: str | None = None
    offer_location: str | None = None
    offer_remote_mode: str | None = None


class PersonalizationBrief(BaseModel):
    target: BriefTarget
    qualification: BriefQualification
    strengths: list[Strength]
    do_not_claim: list[DoNotClaim]
    open_questions: list[OpenQuestion]
    emphasis_candidates: list[EmphasisCandidate]
    company_context: CompanyContext
