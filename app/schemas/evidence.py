from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.models.enums import Confidence, EvidenceTargetType, SourceType
from app.schemas.common import LongText, ORMModel, ShortStr, TimestampedRead


def check_source_uri(value: str) -> str:
    """Either an http(s) URL, or a path relative to the private data directory."""
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL sources must be http(s)")
        return value
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or value.startswith("~"):
        raise ValueError("local sources must be relative to the private data directory")
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError("local sources must not contain '..'")
    return value


SourceUri = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(check_source_uri),
]


class EvidenceCreate(BaseModel):
    source_type: SourceType
    source_name: ShortStr
    source_uri: SourceUri | None = None
    extracted_text: LongText | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    # Conservative defaults: evidence is neither verified nor trusted until stated otherwise.
    confidence: Confidence = Confidence.LOW
    verified: bool = False


class EvidenceLinkCreate(BaseModel):
    evidence_id: int
    target_type: EvidenceTargetType
    target_id: int
    note: LongText | None = None


class EvidenceLinkRead(ORMModel):
    id: int
    evidence_id: int
    target_type: EvidenceTargetType
    target_id: int
    note: str | None
    created_at: datetime


class EvidenceRead(EvidenceCreate, TimestampedRead):
    links: list[EvidenceLinkRead]
