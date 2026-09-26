"""Deterministic matching of requirements against the Candidate Brain.

Pure: no database, no network.

The question is never "is the candidate good enough?" but "what does the Brain ESTABLISH about
this requirement?". Four answers, no score:

- `covered`: a **Skill** (never another object) with evidence state `known` or `verified` matches
  the requirement through the taxonomy (canonical name or alias). A Project that mentions a skill
  never becomes a Skill; it can only SUPPORT an already existing one.
- `weak`: the matching fact exists but is `unknown` / `uncertain`: an open question for the human.
- `gap`: the Brain does not establish it. It is NOT "the candidate lacks it".
- `unmeasurable`: the data cannot give a reliable conclusion (year-only dates, an experience
  without end date, a duration scoped to a domain the Brain cannot relate to).

Proximity is not coverage: Tableau never covers Power BI, pandas never covers Python. Coverage
between two DIFFERENT skills exists only where the taxonomy says so explicitly and one way
(`implies`): a Skill PostgreSQL covers a requirement SQL; a Skill SQL does not cover PostgreSQL.

Experience durations use interval arithmetic on PARTIAL dates: a date that only says `2022`
could be any day of 2022, so the duration is bounded, never invented. Coverage needs the
GUARANTEED lower bound to reach the requirement; a requirement that only the upper bound could
reach is `unmeasurable`. No clock is read: the result never depends on today's date.
"""

import calendar
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.core.normalize import normalize_text
from app.models.enums import (
    EvidenceTargetType,
    InformationState,
    MatchFactRole,
    MatchNote,
    MatchStatus,
    RequirementImportance,
    RequirementKind,
)
from app.services.requirement_extraction import RequirementExtractor, experience_years
from app.services.skill_taxonomy import SkillTaxonomy

# Bump when matching rules change: it is part of the qualification fingerprint.
MATCHER_VERSION = "matching-1"

DAYS_PER_YEAR = 365  # every span of N calendar years has at least N * 365 days

_ESTABLISHED = (InformationState.KNOWN, InformationState.VERIFIED)


@dataclass(frozen=True)
class SkillFact:
    id: int
    name: str
    state: InformationState


@dataclass(frozen=True)
class TextFact:
    """A Project or an Experience seen as text that may mention a skill."""

    id: int
    type: EvidenceTargetType
    state: InformationState
    text: str


@dataclass(frozen=True)
class ExperienceFact:
    id: int
    state: InformationState
    start: str | None  # partial date as stored: YYYY, YYYY-MM or YYYY-MM-DD
    end: str | None
    text: str


@dataclass(frozen=True)
class BrainSnapshot:
    """Everything matching may read from the Brain (and nothing else)."""

    skills: tuple[SkillFact, ...] = ()
    projects: tuple[TextFact, ...] = ()
    experiences: tuple[ExperienceFact, ...] = ()

    def digest(self) -> str:
        payload = {
            "skills": [[s.id, s.name, s.state.value] for s in sorted(self.skills, key=_by_id)],
            "projects": [[p.id, p.text, p.state.value] for p in sorted(self.projects, key=_by_id)],
            "experiences": [
                [e.id, e.text, e.start, e.end, e.state.value]
                for e in sorted(self.experiences, key=_by_id)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _HasId(Protocol):
    @property
    def id(self) -> int: ...


def _by_id(item: _HasId) -> int:
    return item.id


@dataclass(frozen=True)
class RequirementSpec:
    """What matching (and the fingerprint) may read from a requirement."""

    id: int
    kind: RequirementKind
    key: str
    label: str
    importance: RequirementImportance
    value: str | None = None
    qualifier: str | None = None


@dataclass(frozen=True)
class FactRef:
    type: EvidenceTargetType
    id: int
    state: InformationState
    role: MatchFactRole


@dataclass(frozen=True)
class MatchResult:
    requirement_id: int
    status: MatchStatus
    note: MatchNote
    facts: tuple[FactRef, ...] = ()


@dataclass(frozen=True)
class MatchingInputs:
    """Everything a set of matches depends on; hashed into the qualification fingerprint."""

    requirements: tuple[RequirementSpec, ...] = ()
    brain: BrainSnapshot = BrainSnapshot()
    taxonomy_version: str = ""

    def payload(self) -> dict[str, object]:
        if not self.requirements:
            return {}  # nothing to match: the Brain is not an input of this qualification
        return {
            "matcher": MATCHER_VERSION,
            "taxonomy": self.taxonomy_version,
            "brain": self.brain.digest(),
            "requirements": [
                [r.id, r.kind.value, r.key, r.importance.value, r.value, r.qualifier]
                for r in sorted(self.requirements, key=_by_id)
            ],
        }


# --- Experience: interval arithmetic on partial dates ---------------------------------------


def earliest(value: str) -> date:
    """First day the partial date can designate. Only used to BOUND, never to state a date."""
    parts = [int(part) for part in value.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def latest(value: str) -> date:
    parts = [int(part) for part in value.split("-")]
    month = parts[1] if len(parts) > 1 else 12
    day = parts[2] if len(parts) > 2 else calendar.monthrange(parts[0], month)[1]
    return date(parts[0], month, day)


Interval = tuple[date, date]


def _union_days(intervals: Iterable[Interval]) -> int:
    total = 0
    current: Interval | None = None
    for begin, end in sorted(i for i in intervals if i[0] < i[1]):
        if current is None:
            current = (begin, end)
        elif begin <= current[1]:
            current = (current[0], max(current[1], end))
        else:
            total += (current[1] - current[0]).days
            current = (begin, end)
    if current is not None:
        total += (current[1] - current[0]).days
    return total


def _certain(fact: ExperienceFact) -> Interval | None:
    """The span the experience CERTAINLY covers (needs both dates)."""
    if not fact.start or not fact.end:
        return None
    begin, end = latest(fact.start), earliest(fact.end)
    return (begin, end) if begin < end else None


def _possible_days(facts: Sequence[ExperienceFact]) -> int | None:
    """Upper bound of the duration; None = unbounded (a missing date leaves it open)."""
    if any(not fact.start or not fact.end for fact in facts):
        return None
    return _union_days((earliest(f.start or ""), latest(f.end or "")) for f in facts)


def _experience_ref(fact: ExperienceFact, role: MatchFactRole) -> FactRef:
    return FactRef(EvidenceTargetType.EXPERIENCE, fact.id, fact.state, role)


# --- Matching -------------------------------------------------------------------------------


class RequirementMatcher:
    def __init__(self, taxonomy: SkillTaxonomy, extractor: RequirementExtractor) -> None:
        self._taxonomy = taxonomy
        self._extractor = extractor

    def match_all(
        self, requirements: Sequence[RequirementSpec], brain: BrainSnapshot
    ) -> list[MatchResult]:
        mentioned = {
            (fact.type, fact.id): self._extractor.mentioned_keys(fact.text)
            for fact in (*brain.projects, *(_as_text(e) for e in brain.experiences))
        }
        return [
            self._match(requirement, brain, mentioned)
            for requirement in sorted(requirements, key=lambda item: item.id)
        ]

    def _match(
        self,
        requirement: RequirementSpec,
        brain: BrainSnapshot,
        mentioned: dict[tuple[EvidenceTargetType, int], frozenset[str]],
    ) -> MatchResult:
        if requirement.kind is RequirementKind.EXPERIENCE:
            return self._match_experience(requirement, brain)
        return self._match_skill(requirement, brain, mentioned)

    # --- skills -------------------------------------------------------------------------

    def _mentions(self, key: str, fact: TextFact, mentioned: frozenset[str]) -> bool:
        if self._taxonomy.get(key) is not None:
            return key in mentioned
        return f" {key} " in f" {normalize_text(fact.text)} "  # a label outside the taxonomy

    def _match_skill(
        self,
        requirement: RequirementSpec,
        brain: BrainSnapshot,
        mentioned: dict[tuple[EvidenceTargetType, int], frozenset[str]],
    ) -> MatchResult:
        key = requirement.key
        # The requirement itself, plus the entries that EXPLICITLY cover it (`implies`: a Skill
        # PostgreSQL covers SQL). Proximity (`related`) is never part of this set.
        accepted = {key, *self._taxonomy.covering_keys(key)}
        skills = [s for s in brain.skills if self._taxonomy.canonical_key(s.name) in accepted]
        talkers = [
            fact
            for fact in (*brain.projects, *(_as_text(e) for e in brain.experiences))
            if any(self._mentions(k, fact, mentioned[(fact.type, fact.id)]) for k in accepted)
        ]
        established = [s for s in skills if s.state in _ESTABLISHED]

        def refs(role: MatchFactRole, facts: Iterable[SkillFact | TextFact]) -> list[FactRef]:
            found = []
            for fact in facts:
                kind = EvidenceTargetType.SKILL if isinstance(fact, SkillFact) else fact.type
                found.append(FactRef(kind, fact.id, fact.state, role))
            return found

        if established:
            facts = refs(MatchFactRole.ESTABLISHES, established) + refs(
                MatchFactRole.SUPPORTS, talkers
            )
            direct = any(self._taxonomy.canonical_key(s.name) == key for s in established)
            note = MatchNote.SKILL_ESTABLISHED if direct else MatchNote.SKILL_IMPLIED
            return MatchResult(requirement.id, MatchStatus.COVERED, note, tuple(facts))
        if skills:  # a Skill exists but its evidence is unknown / uncertain: open question
            facts = refs(MatchFactRole.ESTABLISHES, skills) + refs(MatchFactRole.SUPPORTS, talkers)
            return MatchResult(
                requirement.id, MatchStatus.WEAK, MatchNote.SKILL_UNCONFIRMED, tuple(facts)
            )
        if talkers:  # mentioned by a project / experience, but never established as a Skill
            return MatchResult(
                requirement.id,
                MatchStatus.GAP,
                MatchNote.MENTIONED_BY_PROJECT_ONLY,
                tuple(refs(MatchFactRole.MENTIONS, talkers)),
            )
        return MatchResult(requirement.id, MatchStatus.GAP, MatchNote.NO_SKILL_IN_BRAIN)

    # --- experience ---------------------------------------------------------------------

    def _match_experience(self, requirement: RequirementSpec, brain: BrainSnapshot) -> MatchResult:
        def result(
            status: MatchStatus, note: MatchNote, facts: Iterable[FactRef] = ()
        ) -> MatchResult:
            return MatchResult(requirement.id, status, note, tuple(facts))

        years = experience_years(requirement.value)
        if years is None:
            return result(MatchStatus.UNMEASURABLE, MatchNote.EXPERIENCE_VALUE_UNREADABLE)
        if requirement.qualifier:
            # "2 years in <domain>": the Brain does not say which experience relates to it
            return result(MatchStatus.UNMEASURABLE, MatchNote.EXPERIENCE_SCOPE_NOT_MEASURABLE)
        needed = years * DAYS_PER_YEAR
        if not brain.experiences:
            return result(MatchStatus.GAP, MatchNote.EXPERIENCE_NONE_IN_BRAIN)

        established = [e for e in brain.experiences if e.state in _ESTABLISHED]
        unconfirmed = [e for e in brain.experiences if e.state not in _ESTABLISHED]
        certain = [span for span in map(_certain, established) if span is not None]
        if _union_days(certain) >= needed:
            contributing = [e for e in established if _certain(e) is not None]
            return result(
                MatchStatus.COVERED,
                MatchNote.EXPERIENCE_ESTABLISHED,
                (_experience_ref(e, MatchFactRole.ESTABLISHES) for e in contributing),
            )
        possible = _possible_days(established) if established else 0
        if possible is None or possible >= needed:
            # Only the missing precision (or a missing date) stands between us and an answer.
            return result(
                MatchStatus.UNMEASURABLE,
                MatchNote.EXPERIENCE_DATES_INSUFFICIENT,
                (_experience_ref(e, MatchFactRole.ESTABLISHES) for e in established),
            )
        every_possible = _possible_days(brain.experiences)
        if unconfirmed and (every_possible is None or every_possible >= needed):
            return result(
                MatchStatus.WEAK,
                MatchNote.EXPERIENCE_UNCONFIRMED,
                (_experience_ref(e, MatchFactRole.ESTABLISHES) for e in unconfirmed),
            )
        return result(
            MatchStatus.GAP,
            MatchNote.EXPERIENCE_INSUFFICIENT,
            (_experience_ref(e, MatchFactRole.ESTABLISHES) for e in established),
        )


def _as_text(experience: ExperienceFact) -> TextFact:
    return TextFact(experience.id, EvidenceTargetType.EXPERIENCE, experience.state, experience.text)
