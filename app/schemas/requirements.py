"""Schemas of requirements and of their matches. There is deliberately NO score anywhere."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, StringConstraints, model_validator

from app.models import RequirementMatch, RequirementMatchFact
from app.models.enums import (
    EvidenceTargetType,
    InformationState,
    MatchFactRole,
    MatchNote,
    MatchStatus,
    RequirementImportance,
    RequirementKind,
    RequirementOrigin,
)
from app.schemas.common import ORMModel
from app.schemas.targets import SourceInput, SourceRead

Excerpt = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
DurationText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class RequirementRead(ORMModel):
    id: int
    target_id: int
    kind: RequirementKind
    key: str
    label: str
    importance: RequirementImportance
    origin: RequirementOrigin
    source_field: str
    source: SourceRead
    source_hash: str
    excerpt: str
    value: str | None
    qualifier: str | None
    extractor_version: str | None
    created_at: datetime
    active: bool
    deactivated_at: datetime | None
    superseded_by_id: int | None


class ManualRequirementCreate(BaseModel):
    """A requirement typed by the human. It needs its exact excerpt and where it comes from."""

    kind: Literal[RequirementKind.SKILL, RequirementKind.EXPERIENCE] = RequirementKind.SKILL
    label: ShortText
    importance: RequirementImportance = RequirementImportance.UNSPECIFIED
    excerpt: Excerpt
    value: DurationText | None = None  # experience only, as written: "2 ans"
    qualifier: ShortText | None = None  # experience only, as written: "en data analysis"
    source: SourceInput

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if not self.excerpt.strip():
            raise ValueError("the excerpt must contain the source text")
        if not (self.source.reference or self.source.url):
            raise ValueError("a manual requirement must say where it comes from (reference or url)")
        if self.kind is RequirementKind.EXPERIENCE and not self.value:
            raise ValueError("an experience requirement needs its duration as written")
        if self.kind is RequirementKind.SKILL and (self.value or self.qualifier):
            raise ValueError("value and qualifier only apply to an experience requirement")
        return self


class RequirementWriteResult(BaseModel):
    requirement: RequirementRead
    created: bool


ExtractionOutcome = Literal["extracted", "no_offer", "no_description"]


class ExtractionReport(BaseModel):
    """What one explicit extraction did. Counters only, never the offer text."""

    target_id: int
    # `no_offer` = spontaneous target; `no_description` = an offer without text.
    outcome: ExtractionOutcome
    found: int
    created: int
    unchanged: int
    superseded: int  # an offer requirement whose content changed: new version, old deactivated
    retired: int  # an offer requirement no longer present in the text: deactivated, not deleted
    kept_manual: int  # found in the text too, but a human-entered requirement is kept as is
    requirements: list[RequirementRead]


# --- Matches ----------------------------------------------------------------------------------

_NOTE_TEXT: dict[MatchNote, str] = {
    MatchNote.SKILL_ESTABLISHED: "A Skill with known or verified evidence matches this skill",
    MatchNote.SKILL_IMPLIED: (
        "A more specific Skill with known or verified evidence explicitly covers this skill"
    ),
    MatchNote.SKILL_UNCONFIRMED: (
        "A matching Skill exists but its evidence is unknown or uncertain: to confirm"
    ),
    MatchNote.NO_SKILL_IN_BRAIN: "Not established by the Candidate Brain (no matching Skill)",
    MatchNote.MENTIONED_BY_PROJECT_ONLY: (
        "Mentioned by a project or experience, but no Skill establishes it: to confirm"
    ),
    MatchNote.EXPERIENCE_ESTABLISHED: (
        "Dated experiences with known or verified evidence certainly cover this duration"
    ),
    MatchNote.EXPERIENCE_INSUFFICIENT: (
        "The dated experiences of the Brain cannot reach this duration"
    ),
    MatchNote.EXPERIENCE_NONE_IN_BRAIN: "No experience is recorded in the Candidate Brain",
    MatchNote.EXPERIENCE_UNCONFIRMED: (
        "Reaching this duration depends on experiences whose evidence is unknown or uncertain"
    ),
    MatchNote.EXPERIENCE_DATES_INSUFFICIENT: (
        "The dates of the experiences are too imprecise (or open-ended) to conclude"
    ),
    MatchNote.EXPERIENCE_SCOPE_NOT_MEASURABLE: (
        "The duration is tied to a domain; the Brain does not say which experience relates to it"
    ),
    MatchNote.EXPERIENCE_VALUE_UNREADABLE: "The duration written in the offer cannot be read",
}


def render_note(note: MatchNote) -> str:
    """Fixed template: no free text is stored or generated."""
    return _NOTE_TEXT[note]


class MatchFactRead(BaseModel):
    """A reference to a Brain fact, with its evidence state when the match was computed."""

    type: EvidenceTargetType
    id: int
    name: str
    state: InformationState
    role: MatchFactRole


class RequirementMatchRead(BaseModel):
    id: int
    requirement: RequirementRead
    status: MatchStatus
    note: MatchNote
    text: str
    facts: list[MatchFactRead]


class RequirementCounters(BaseModel):
    """Raw counters. Never combined into one figure, a ratio or a score."""

    requirements_total: int = 0
    requirements_covered: int = 0
    requirements_weak: int = 0
    requirements_gap: int = 0
    requirements_unmeasurable: int = 0


def read_fact(fact: RequirementMatchFact) -> MatchFactRead:
    return MatchFactRead(
        type=fact.fact_type,
        id=fact.fact_id,
        name=fact.fact_name,
        state=fact.state,
        role=fact.role,
    )


def read_match(match: RequirementMatch) -> RequirementMatchRead:
    return RequirementMatchRead(
        id=match.id,
        requirement=RequirementRead.model_validate(match.requirement),
        status=match.status,
        note=match.note,
        text=render_note(match.note),
        facts=[read_fact(fact) for fact in match.facts],
    )


def count_matches(matches: list[RequirementMatchRead]) -> RequirementCounters:
    counters = RequirementCounters(requirements_total=len(matches))
    for match in matches:
        name = f"requirements_{match.status.value}"
        setattr(counters, name, getattr(counters, name) + 1)
    return counters
