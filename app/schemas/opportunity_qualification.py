""" "Criteria v1" presentation schemas (step 3a) - see docs/qualification_v1.md.

These wrap the EXISTING, unchanged `Qualification`/`QualificationRead` (`app.schemas.qualification`)
with the vocabulary the spec asks for (`qualified` / `not_qualified` / `uncertain`) and the two
non-gating labels (`RoleFamily`, `LocationTier`). Nothing here is stored: `DECISION_LABELS` is a
display mapping of the existing, unchanged `QualificationStatus`
(`candidate`/`excluded`/`needs_information`), never a new stored status - the database keeps
exactly one vocabulary, so six-plus existing steps that already depend on `QualificationStatus`
need no change. No score, no percentage, anywhere below.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import LocationTier, QualificationStatus, RoleFamily
from app.schemas.qualification import QualificationRead

DecisionLabel = Literal["qualified", "not_qualified", "uncertain"]

# The ONLY place this mapping exists: CLI and API both import it, never re-derive it.
DECISION_LABELS: dict[QualificationStatus, DecisionLabel] = {
    QualificationStatus.CANDIDATE: "qualified",
    QualificationStatus.EXCLUDED: "not_qualified",
    QualificationStatus.NEEDS_INFORMATION: "uncertain",
}


class V1Reason(BaseModel):
    """One structured reason (spec section 14): a criterion, its result, and what was expected
    versus what was actually observed - never free text, always a reference to a real result."""

    criterion: str  # the criterion's dimension, e.g. "contract_type"
    result: str  # the criterion's outcome, e.g. "satisfied" / "not_matched" / "unknown"
    expected: list[str]  # the criterion's own declared values
    actual: str | None  # what was actually observed, if anything (never invented)


class V1QualificationRead(BaseModel):
    target_id: int
    criteria_version: str
    decision: DecisionLabel
    stale: bool
    created_at: datetime
    role_family: RoleFamily
    role_family_matched_term: str | None
    location_tier: LocationTier
    reasons: list[V1Reason]


class V1OpportunitySummary(BaseModel):
    """One row of `GET /api/opportunities/qualified` or `.../uncertain`."""

    target_id: int
    company_name: str
    offer_title: str | None
    decision: DecisionLabel
    role_family: RoleFamily
    location_tier: LocationTier
    created_at: datetime


def to_v1_reasons(qualification: QualificationRead) -> list[V1Reason]:
    return [
        V1Reason(
            criterion=result.criterion.dimension.value,
            result=result.outcome.value,
            expected=list(result.criterion.values),
            actual=result.observed,
        )
        for result in qualification.results
    ]
