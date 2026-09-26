from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.models.enums import EvidenceTargetType, ProposalStatus
from app.schemas.common import LongText, TimestampedRead
from app.schemas.evidence import check_source_uri


def _check_private_relative_path(value: str) -> str:
    if "://" in value:
        raise ValueError("must be a path relative to the private data directory, not a URL")
    return check_source_uri(value)


PrivateRelativePath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(_check_private_relative_path),
]


class CVIngestionRequest(BaseModel):
    """`source_path` is relative to the private data directory (e.g. `documents/cv.docx`)."""

    source_path: PrivateRelativePath


class ProposalRead(TimestampedRead):
    ingestion_id: int
    kind: EvidenceTargetType
    status: ProposalStatus
    data: dict[str, Any]
    reviewed_data: dict[str, Any] | None
    source_excerpt: str
    uncertainties: list[str]
    review_note: str | None
    decided_at: datetime | None
    resulting_fact_id: int | None
    linked_existing: bool


class IngestionSummaryRead(TimestampedRead):
    source_uri: str
    sha256: str
    file_size: int
    parser_version: str
    stats: dict[str, Any]
    evidence_id: int | None


class IngestionRead(IngestionSummaryRead):
    proposals: list[ProposalRead]


class ProposalAccept(BaseModel):
    """Human validation of a proposal.

    `corrections` overrides extracted fields (only fields of the target fact schema).
    `acknowledge_uncertainties` must be true when the proposal lists uncertainties.
    Accepting is NOT independent verification: the resulting evidence is never `verified`.
    """

    corrections: dict[str, Any] = Field(default_factory=dict)
    acknowledge_uncertainties: bool = False


class ProposalReject(BaseModel):
    note: LongText | None = None
