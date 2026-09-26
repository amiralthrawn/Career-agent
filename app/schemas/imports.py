from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.ingestion import PrivateRelativePath


class ImportRequest(BaseModel):
    """`source_path` is relative to the private data directory, e.g. `imports/targets.csv`."""

    source_path: PrivateRelativePath
    # Optional guard for `apply`: refuse if the file is not the one that was previewed.
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None


class RowReport(BaseModel):
    """Outcome of one CSV row. Only field names and counters, never the row's content."""

    row: int  # 1-based data row number (the header is not counted)
    outcome: Literal["created", "matched", "error"]
    target_id: int | None = None  # None in a preview (nothing is written)
    company: Literal["created", "matched"] | None = None
    opportunity: Literal["created", "matched"] | None = None
    contacts_created: int = 0
    contacts_matched: int = 0
    channels_created: int = 0
    # e.g. "company.location: differs": an existing value is never overwritten.
    differences: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []


class ImportReport(BaseModel):
    dry_run: bool
    sha256: str
    delimiter: Literal[",", ";"]
    rows_total: int
    created: int
    matched: int
    rejected: int
    companies_created: int
    opportunities_created: int
    contacts_created: int
    channels_created: int
    ignored_columns: list[str]
    rows: list[RowReport]
