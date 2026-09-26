"""PersonalizationBrief builder (step 3b). Deterministic, computed on demand, never persisted.

The brief restates what the latest qualification already established, sorted into four lists:

- `strengths`: requirements the Brain truly covers, with the facts (and their evidence state);
- `do_not_claim`: gaps, weak and unmeasurable requirements: nothing may assert them;
- `open_questions`: what only the human can settle;
- `emphasis_candidates`: established facts ordered by how many covered requirements they support
  (count, then a fixed type order, then id). Explainable, not a score.

No network, no LLM, no clock, no information beyond the Brain, the target and the qualification.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Target
from app.models.enums import EvidenceTargetType, InformationState, MatchStatus
from app.schemas.personalization import (
    QUESTION_BY_NOTE,
    QUESTION_TEXT,
    BriefCompany,
    BriefFact,
    BriefOffer,
    BriefQualification,
    BriefSource,
    BriefTarget,
    CompanyContext,
    DoNotClaim,
    EmphasisCandidate,
    EmphasisFact,
    OpenQuestion,
    PersonalizationBrief,
    RequirementRef,
    Strength,
)
from app.schemas.qualification import QualificationRead, read_qualification
from app.schemas.requirements import MatchFactRead, RequirementMatchRead
from app.services.criteria_evaluation import build_view
from app.services.qualification import QualificationService

# Ties between facts that support as many requirements are broken by type, then id.
_TYPE_ORDER = {
    EvidenceTargetType.PROJECT: 0,
    EvidenceTargetType.EXPERIENCE: 1,
    EvidenceTargetType.SKILL: 2,
}
_ESTABLISHED = (InformationState.KNOWN, InformationState.VERIFIED)


def _ref(match: RequirementMatchRead) -> RequirementRef:
    requirement = match.requirement
    return RequirementRef(
        id=requirement.id,
        kind=requirement.kind,
        key=requirement.key,
        label=requirement.label,
        importance=requirement.importance,
        origin=requirement.origin,
        excerpt=requirement.excerpt,
    )


def _fact(fact: MatchFactRead) -> BriefFact:
    return BriefFact(type=fact.type, id=fact.id, name=fact.name, state=fact.state, role=fact.role)


def _emphasis(matches: list[RequirementMatchRead]) -> list[EmphasisCandidate]:
    supported: dict[tuple[EvidenceTargetType, int], set[int]] = defaultdict(set)
    names: dict[tuple[EvidenceTargetType, int], MatchFactRead] = {}
    for match in matches:
        if match.status is not MatchStatus.COVERED:
            continue
        for fact in match.facts:
            if fact.state in _ESTABLISHED:  # never put forward a fact the Brain cannot back
                supported[(fact.type, fact.id)].add(match.requirement.id)
                names[(fact.type, fact.id)] = fact
    ordered = sorted(supported, key=lambda key: (-len(supported[key]), _TYPE_ORDER[key[0]], key[1]))
    return [
        EmphasisCandidate(
            rank=position,
            fact=EmphasisFact(type=key[0], id=key[1], name=names[key].name, state=names[key].state),
            covered_requirement_ids=sorted(supported[key]),
            covered_count=len(supported[key]),
        )
        for position, key in enumerate(ordered, start=1)
    ]


def build_brief(target: Target, qualification: QualificationRead) -> PersonalizationBrief:
    offer = target.opportunity
    company = target.company
    view = build_view(target)
    matches = sorted(qualification.requirement_matches, key=lambda item: item.requirement.id)

    strengths = [
        Strength(requirement=_ref(m), facts=[_fact(f) for f in m.facts])
        for m in matches
        if m.status is MatchStatus.COVERED
    ]
    do_not_claim = [
        DoNotClaim(
            requirement=_ref(m),
            status=m.status,
            note=m.note,
            text=m.text,
            facts=[_fact(f) for f in m.facts],
        )
        for m in matches
        if m.status is not MatchStatus.COVERED
    ]
    open_questions = [
        OpenQuestion(
            question=QUESTION_BY_NOTE[m.note],
            requirement=_ref(m),
            facts=[_fact(f) for f in m.facts],
            text=QUESTION_TEXT[QUESTION_BY_NOTE[m.note]].format(label=m.requirement.label),
        )
        for m in matches
        if m.note in QUESTION_BY_NOTE
    ]
    return PersonalizationBrief(
        target=BriefTarget(
            target_id=target.id,
            mode=target.mode,
            company=BriefCompany(id=company.id, name=company.name),
            offer=(BriefOffer(id=offer.id, title=offer.title, url=offer.url) if offer else None),
            contract_type=view.contract_type,
            source=BriefSource(
                kind=target.source.kind,
                label=target.source.label,
                url=target.source.url,
                reference=target.source.reference,
            ),
        ),
        qualification=BriefQualification(
            id=qualification.id,
            profile_id=qualification.profile_id,
            status=qualification.status,
            stale=qualification.stale,
            inputs_fingerprint=qualification.inputs_fingerprint,
        ),
        strengths=strengths,
        do_not_claim=do_not_claim,
        open_questions=open_questions,
        emphasis_candidates=_emphasis(matches),
        company_context=CompanyContext(
            sector=company.sector,
            location=company.location,
            country_code=company.country_code,
            domain=company.domain,
            website_url=company.website_url,
            careers_url=company.careers_url,
            offer_location=offer.location if offer else None,
            offer_remote_mode=offer.remote_mode.value if offer and offer.remote_mode else None,
        ),
    )


class PersonalizationService:
    def __init__(self, session: Session) -> None:
        self._qualifications = QualificationService(session)

    def brief(self, target_id: int, profile_id: int | None = None) -> PersonalizationBrief:
        """Brief of the latest qualification of the target (404 when it was never qualified)."""
        current = self._qualifications.current(target_id, profile_id)
        target = self._qualifications.target(target_id)
        return build_brief(target, read_qualification(current.qualification, stale=current.stale))
