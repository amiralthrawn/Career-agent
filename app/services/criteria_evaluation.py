"""Deterministic evaluation of search criteria against a target. Pure: no database, no network.

The evaluation is EPISTEMIC, not a score. For each criterion it answers "what do we know?":

- `satisfied`: compatible, with evidence;
- `incompatible`: a KNOWN incompatibility. Only closed vocabularies (contract type, country) or
  an excluded value that is actually present can prove one;
- `not_matched`: free text was available but shows no match. Wording differs, a suburb is not
  the city, a sector is described differently: this is NOT proof of incompatibility, so it can
  never exclude a target;
- `unknown`: the data needed is missing. Missing data is never a violation.

The LEVEL of a criterion then decides its effect (see `decide_status`): only a required
criterion that is known to be incompatible excludes a target. Preferred and flexible criteria
never exclude.

The same code evaluates targets with an offer and spontaneous targets (no offer): whatever the
offer would provide is `unknown` (`no_offer`), never a violation.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from app.core.normalize import normalize_domain, normalize_name, normalize_text
from app.models import Target
from app.models.enums import (
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    CriterionOutcome,
    EvaluationCode,
    QualificationStatus,
    ReasonCode,
)
from app.services.requirement_matching import MatchingInputs

# Bump when the evaluation rules change: it is part of the inputs fingerprint, so previous
# qualifications become stale.
EVALUATOR_VERSION = "criteria-3"

MAX_OBSERVED_CHARS = 255


@dataclass(frozen=True)
class CriterionSpec:
    id: int
    dimension: CriterionDimension
    operator: CriterionOperator
    values: tuple[str, ...]
    level: CriterionLevel


@dataclass(frozen=True)
class TargetView:
    """What the evaluation may look at (and nothing else); also what the fingerprint hashes."""

    target_id: int
    contract_type: str | None  # the application's contract, else the offer's
    has_offer: bool
    offer_title: str | None
    offer_location: str | None
    offer_remote_mode: str | None
    offer_description: str | None
    company_name_key: str
    company_domain: str | None
    company_location: str | None
    company_country: str | None
    company_sector: str | None


@dataclass(frozen=True)
class Evaluation:
    outcome: CriterionOutcome
    code: EvaluationCode
    observed: str | None = None


@dataclass(frozen=True)
class ReasonSpec:
    code: ReasonCode
    result_index: int | None  # index in the results list, None for target-level reasons


def build_view(target: Target) -> TargetView:
    offer = target.opportunity
    company = target.company
    contract = target.contract_type or (offer.contract_type if offer else None)
    return TargetView(
        target_id=target.id,
        contract_type=contract.value if contract else None,
        has_offer=offer is not None,
        offer_title=offer.title if offer else None,
        offer_location=offer.location if offer else None,
        offer_remote_mode=offer.remote_mode.value if offer and offer.remote_mode else None,
        offer_description=offer.description_text if offer else None,
        company_name_key=company.name_key,
        company_domain=company.domain,
        company_location=company.location,
        company_country=company.country_code,
        company_sector=company.sector,
    )


def fingerprint(
    profile_id: int,
    criteria: Sequence[CriterionSpec],
    view: TargetView,
    matching: MatchingInputs | None = None,
) -> str:
    """Hash of everything the qualification used. Same inputs -> same fingerprint.

    `matching` (step 3b) adds the requirements and the Candidate Brain snapshot they are matched
    against: a change of either makes the qualification stale. Without requirements it is empty.
    """
    payload = {
        "evaluator": EVALUATOR_VERSION,
        "profile": profile_id,
        "criteria": [
            [c.id, c.dimension.value, c.operator.value, list(c.values), c.level.value]
            for c in sorted(criteria, key=lambda item: item.id)
        ],
        "view": asdict(view),
        "matching": matching.payload() if matching else {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# --- Matching helpers ------------------------------------------------------------------


def _terms(values: Sequence[str]) -> list[str]:
    return [term for term in (normalize_text(value) for value in values) if term]


def _found(text: str, terms: Sequence[str]) -> list[str]:
    """Terms present in `text` as whole words/phrases (accent- and case-insensitive)."""
    padded = f" {normalize_text(text)} "
    return [term for term in terms if f" {term} " in padded]


def _short(value: str | None) -> str | None:
    return value[:MAX_OBSERVED_CHARS] if value else None


def _closed(criterion: CriterionSpec, observed: str | None, allowed: Sequence[str]) -> Evaluation:
    """Closed vocabulary: a mismatch IS a known incompatibility."""
    if observed is None:
        return Evaluation(CriterionOutcome.UNKNOWN, EvaluationCode.DATA_MISSING)
    present = observed in allowed
    if criterion.operator is CriterionOperator.ANY_OF:
        if present:
            return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.MATCH, observed)
        return Evaluation(
            CriterionOutcome.INCOMPATIBLE, EvaluationCode.NOT_IN_ALLOWED_SET, observed
        )
    if present:
        return Evaluation(
            CriterionOutcome.INCOMPATIBLE, EvaluationCode.EXCLUDED_VALUE_PRESENT, observed
        )
    return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.EXCLUDED_TERM_ABSENT, observed)


def _open_text(
    criterion: CriterionSpec,
    text: str | None,
    missing: EvaluationCode = EvaluationCode.DATA_MISSING,
) -> Evaluation:
    """Free text that is fully known: a match proves compatibility, a miss proves nothing."""
    if text is None or not normalize_text(text):
        return Evaluation(CriterionOutcome.UNKNOWN, missing)
    found = _found(text, _terms(criterion.values))
    observed = _short(normalize_text(text))
    if criterion.operator is CriterionOperator.ANY_OF:
        if found:
            return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.MATCH, observed)
        return Evaluation(CriterionOutcome.NOT_MATCHED, EvaluationCode.NO_MATCH_FOUND, observed)
    if found:
        return Evaluation(
            CriterionOutcome.INCOMPATIBLE, EvaluationCode.EXCLUDED_VALUE_PRESENT, observed
        )
    return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.EXCLUDED_TERM_ABSENT, observed)


def _keyword(criterion: CriterionSpec, view: TargetView) -> Evaluation:
    """Terms searched in the offer title/description and the company sector.

    The absence of a term only means something when the whole offer text is available.
    """
    parts = [p for p in (view.offer_title, view.offer_description, view.company_sector) if p]
    found: list[str] = []
    for part in parts:
        for term in _found(part, _terms(criterion.values)):
            if term not in found:
                found.append(term)
    complete = view.offer_description is not None
    incomplete_code = (
        EvaluationCode.DESCRIPTION_NOT_PROVIDED if view.has_offer else EvaluationCode.NO_OFFER
    )
    if criterion.operator is CriterionOperator.ANY_OF:
        if found:
            return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.MATCH, found[0])
        if complete:
            return Evaluation(CriterionOutcome.NOT_MATCHED, EvaluationCode.NO_MATCH_FOUND)
        return Evaluation(CriterionOutcome.UNKNOWN, incomplete_code)
    if found:
        return Evaluation(
            CriterionOutcome.INCOMPATIBLE, EvaluationCode.EXCLUDED_VALUE_PRESENT, found[0]
        )
    if complete:
        return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.EXCLUDED_TERM_ABSENT)
    return Evaluation(CriterionOutcome.UNKNOWN, incomplete_code)


def _company(criterion: CriterionSpec, view: TargetView) -> Evaluation:
    """Identity of the company: a name (accent/legal-form insensitive) or a domain.

    A list of wanted companies is not exhaustive, so a miss is `not_matched` (it cannot
    exclude); to block companies use `none_of`.
    """
    hit = any(
        normalize_name(value) == view.company_name_key
        or (view.company_domain is not None and normalize_domain(value) == view.company_domain)
        for value in criterion.values
    )
    observed = view.company_name_key
    if criterion.operator is CriterionOperator.ANY_OF:
        if hit:
            return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.MATCH, observed)
        return Evaluation(CriterionOutcome.NOT_MATCHED, EvaluationCode.NO_MATCH_FOUND, observed)
    if hit:
        return Evaluation(
            CriterionOutcome.INCOMPATIBLE, EvaluationCode.EXCLUDED_VALUE_PRESENT, observed
        )
    return Evaluation(CriterionOutcome.SATISFIED, EvaluationCode.EXCLUDED_TERM_ABSENT, observed)


def evaluate(criterion: CriterionSpec, view: TargetView) -> Evaluation:
    match criterion.dimension:
        case CriterionDimension.CONTRACT_TYPE:
            return _closed(criterion, view.contract_type, [v.lower() for v in criterion.values])
        case CriterionDimension.COUNTRY:
            country = view.company_country.upper() if view.company_country else None
            return _closed(criterion, country, [v.upper() for v in criterion.values])
        case CriterionDimension.COMPANY:
            return _company(criterion, view)
        case CriterionDimension.SECTOR:
            return _open_text(criterion, view.company_sector)
        case CriterionDimension.LOCATION:
            # The job's place: the offer's if there is an offer, else the company's.
            text = view.offer_location if view.has_offer else view.company_location
            return _open_text(criterion, text)
        case CriterionDimension.ROLE:
            if not view.has_offer:
                return Evaluation(CriterionOutcome.UNKNOWN, EvaluationCode.NO_OFFER)
            return _open_text(criterion, view.offer_title)
        case CriterionDimension.KEYWORD:
            return _keyword(criterion, view)
        case CriterionDimension.REMOTE_MODE:
            # Closed vocabulary, read on the offer. Not stated -> unknown (never assumed); a
            # spontaneous target has no offer to state it. A stated mode that is not allowed
            # IS a known incompatibility.
            if not view.has_offer:
                return Evaluation(CriterionOutcome.UNKNOWN, EvaluationCode.NO_OFFER)
            return _closed(criterion, view.offer_remote_mode, [v.lower() for v in criterion.values])
        case CriterionDimension.CONSTRAINT:
            # A declared hard constraint that no data can evaluate yet. Always unknown: a required
            # one keeps every target at `needs_information`, so it is never silently ignored.
            return Evaluation(CriterionOutcome.UNKNOWN, EvaluationCode.NOT_EVALUABLE)


# --- Decision and reasons --------------------------------------------------------------


def decide_status(
    results: Sequence[tuple[CriterionLevel, CriterionOutcome]],
) -> QualificationStatus:
    """Only a KNOWN incompatibility on a REQUIRED criterion excludes; unresolved required
    criteria only ask for more information; preferred and flexible never change the status."""
    required = [outcome for level, outcome in results if level is CriterionLevel.REQUIRED]
    if CriterionOutcome.INCOMPATIBLE in required:
        return QualificationStatus.EXCLUDED
    if any(o in (CriterionOutcome.NOT_MATCHED, CriterionOutcome.UNKNOWN) for o in required):
        return QualificationStatus.NEEDS_INFORMATION
    return QualificationStatus.CANDIDATE


def build_reasons(
    results: Sequence[tuple[CriterionLevel, CriterionOutcome]], *, has_offer: bool
) -> list[ReasonSpec]:
    """Reasons as codes + references to results, in a stable order."""
    order: list[tuple[ReasonCode, CriterionLevel, tuple[CriterionOutcome, ...]]] = [
        (
            ReasonCode.EXCLUDED_BY_REQUIRED,
            CriterionLevel.REQUIRED,
            (CriterionOutcome.INCOMPATIBLE,),
        ),
        (
            ReasonCode.OPEN_QUESTION_REQUIRED,
            CriterionLevel.REQUIRED,
            (CriterionOutcome.NOT_MATCHED, CriterionOutcome.UNKNOWN),
        ),
        (ReasonCode.REQUIRED_SATISFIED, CriterionLevel.REQUIRED, (CriterionOutcome.SATISFIED,)),
        (ReasonCode.PREFERRED_SATISFIED, CriterionLevel.PREFERRED, (CriterionOutcome.SATISFIED,)),
        (ReasonCode.FLEXIBLE_MATCHED, CriterionLevel.FLEXIBLE, (CriterionOutcome.SATISFIED,)),
    ]
    reasons: list[ReasonSpec] = []
    for code, level, outcomes in order:
        for index, (result_level, outcome) in enumerate(results):
            if result_level is level and outcome in outcomes:
                reasons.append(ReasonSpec(code, index))
    if not has_offer:
        # Informative and NOT negative: no published offer says nothing about hiring.
        reasons.append(ReasonSpec(ReasonCode.NO_OFFER_PUBLISHED, None))
    return reasons
