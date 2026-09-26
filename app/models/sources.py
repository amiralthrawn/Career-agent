"""Provenance: where a piece of information about a company, an offer or a contact comes from.

Every company, offer, target, contact and contact channel points to a `Source`. There is no
"unknown source" and an AI is never a source: the page, API or file it read is.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import SourceKind, enum_column


@dataclass(frozen=True)
class SourceSpec:
    """Description of a source, before it is stored (hashable, so it can be reused per record)."""

    kind: SourceKind
    label: str
    url: str | None = None
    reference: str | None = None


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'import_file' OR reference IS NOT NULL", name="import_needs_reference"
        ),
        CheckConstraint(
            "kind NOT IN ('official_api', 'public_page')"
            " OR url IS NOT NULL OR reference IS NOT NULL",
            name="external_needs_locator",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(enum_column(SourceKind))
    label: Mapped[str] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(2048))
    # Row of an import file, API identifier, or a free note for a manual entry.
    reference: Mapped[str | None] = mapped_column(String(100))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
