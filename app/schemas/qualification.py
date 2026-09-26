"""Schemas of qualifications. There is deliberately NO score, percentage or weight anywhere."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models import Qualification, QualificationReason
from app.models.enums import (
    CriterionLevel,
    CriterionOutcome,
    EvaluationCode,
    QualificationMethod,
    QualificationStatus,
    ReasonCode,
)
from app.schemas.common import ORMModel
from app.schemas.requirements import RequirementMatchRead, count_matches, read_match
from app.schemas.search import SearchCriterionRead

QualificationFilter = Literal["excluded", "needs_information", "candidate", "none"]


class CriterionResultRead(ORMModel):
    id: int
    criterion: SearchCriterionRead
    outcome: CriterionOutcome
    code: EvaluationCode
    observed: str | None


class QualificationReasonRead(BaseModel):
    id: int
    position: int
    code: ReasonCode
    criterion_result_id: int | None
    origin: QualificationMethod
    # Rendered at read time from a fixed template and the referenced result: never stored.
    text: str


class LevelCounters(BaseModel):
    """Raw counters for one level. Never aggregated into a single figure."""

    total: int = 0
    satisfied: int = 0
    incompatible: int = 0
    not_matched: int = 0
    unknown: int = 0


class QualificationRead(BaseModel):
    id: int
    target_id: int
    profile_id: int
    method: QualificationMethod
    status: QualificationStatus
    inputs_fingerprint: str
    created_at: datetime
    # True when the inputs (criteria, target data, evaluator) changed since this was computed.
    stale: bool
    counters: dict[CriterionLevel, LevelCounters]
    results: list[CriterionResultRead]
    reasons: list[QualificationReasonRead]
    # Step 3b: raw counters of the requirement matches. They never change `status`, are never
    # combined with each other or with `counters`, and are 0 for a target without requirements.
    requirements_total: int = 0
    requirements_covered: int = 0
    requirements_weak: int = 0
    requirements_gap: int = 0
    requirements_unmeasurable: int = 0
    requirement_matches: list[RequirementMatchRead] = []


class QualifyResult(BaseModel):
    qualification: QualificationRead
    created: bool


class RunItem(BaseModel):
    target_id: int
    status: QualificationStatus
    created: bool


class RunReport(BaseModel):
    profile_id: int
    targets_processed: int
    created: int
    unchanged: int
    by_status: dict[QualificationStatus, int]
    items: list[RunItem]


class QualifyRequest(BaseModel):
    profile_id: int | None = None  # default: the active profile


class RunRequest(BaseModel):
    profile_id: int | None = None


_TEMPLATES: dict[ReasonCode, str] = {
    ReasonCode.REQUIRED_SATISFIED: "Required {dimension} criterion satisfied ({operator} {values})",
    ReasonCode.EXCLUDED_BY_REQUIRED: (
        "Known incompatibility with a required {dimension} criterion ({operator} {values})"
    ),
    ReasonCode.OPEN_QUESTION_REQUIRED: (
        "Required {dimension} criterion unresolved ({operator} {values}): {result_code}"
    ),
    ReasonCode.PREFERRED_SATISFIED: (
        "Preferred {dimension} criterion satisfied ({operator} {values})"
    ),
    ReasonCode.FLEXIBLE_MATCHED: "Flexible {dimension} criterion matched ({operator} {values})",
    ReasonCode.NO_OFFER_PUBLISHED: (
        "No offer is attached to this target (spontaneous application); this says nothing "
        "about whether the company is hiring"
    ),
}


def render_reason(code: ReasonCode, result: "CriterionResultRead | None") -> str:
    """Fixed template + referenced result. No free text is ever stored or generated here."""
    fields: dict[str, str] = {}
    if result is not None:
        fields = {
            "dimension": result.criterion.dimension.value,
            "operator": result.criterion.operator.value,
            "values": ", ".join(result.criterion.values),
            "result_code": result.code.value,
        }
    return _TEMPLATES[code].format(**fields)


def read_reason(
    reason: QualificationReason, results: dict[int, CriterionResultRead]
) -> QualificationReasonRead:
    result = results.get(reason.criterion_result_id) if reason.criterion_result_id else None
    return QualificationReasonRead(
        id=reason.id,
        position=reason.position,
        code=reason.code,
        criterion_result_id=reason.criterion_result_id,
        origin=reason.origin,
        text=render_reason(reason.code, result),
    )


def read_qualification(qualification: Qualification, *, stale: bool) -> QualificationRead:
    results = [CriterionResultRead.model_validate(item) for item in qualification.results]
    by_id = {result.id: result for result in results}
    counters = {level: LevelCounters() for level in CriterionLevel}
    for result in results:
        bucket = counters[result.criterion.level]
        bucket.total += 1
        setattr(bucket, result.outcome.value, getattr(bucket, result.outcome.value) + 1)
    matches = [read_match(match) for match in qualification.requirement_matches]
    requirement_counters = count_matches(matches)
    return QualificationRead(
        id=qualification.id,
        target_id=qualification.target_id,
        profile_id=qualification.profile_id,
        method=qualification.method,
        status=qualification.status,
        inputs_fingerprint=qualification.inputs_fingerprint,
        created_at=qualification.created_at,
        stale=stale,
        counters=counters,
        results=results,
        reasons=[read_reason(reason, by_id) for reason in qualification.reasons],
        requirement_matches=matches,
        **requirement_counters.model_dump(),
    )
