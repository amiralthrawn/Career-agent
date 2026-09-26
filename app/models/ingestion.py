"""Document ingestion: extracted PROPOSALS awaiting human review.

Nothing here is a fact. A proposal only becomes a fact (Skill, Experience...) when a human
accepts it; pending and rejected proposals are never read as Candidate Brain facts.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.enums import EvidenceTargetType, ProposalStatus, enum_column


class DocumentIngestion(TimestampMixin, CandidateOwnedMixin, Base):
    """One ingestion run of one private document (identified by its SHA-256)."""

    __tablename__ = "document_ingestions"
    __table_args__ = (UniqueConstraint("candidate_id", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Path relative to the private data directory. The document itself is never copied.
    source_uri: Mapped[str] = mapped_column(String(2048))
    sha256: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int]
    parser_version: Mapped[str] = mapped_column(String(50))
    # Non-personal counters only (lines, sections, proposals...).
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Evidence representing the document; created when the first proposal is accepted.
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="SET NULL"), index=True
    )

    proposals: Mapped[list["IngestionProposal"]] = relationship(
        back_populates="ingestion",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="IngestionProposal.id",
    )


class IngestionProposal(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "ingestion_proposals"
    __table_args__ = (UniqueConstraint("ingestion_id", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_id: Mapped[int] = mapped_column(
        ForeignKey("document_ingestions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EvidenceTargetType] = mapped_column(enum_column(EvidenceTargetType))
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus), default=ProposalStatus.PENDING, index=True
    )
    # Fields as extracted (payload of the matching *Create schema). Unknown values are null.
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Fields actually used on acceptance (extraction + human corrections).
    reviewed_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Exact passage of the document that justifies the proposal.
    source_excerpt: Mapped[str] = mapped_column(Text)
    uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Stable hash of the normalised natural key, used to prevent duplicate proposals.
    fingerprint: Mapped[str] = mapped_column(String(64))
    review_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on acceptance. Not a foreign key: the target table depends on `kind`.
    resulting_fact_id: Mapped[int | None]
    # True when acceptance attached evidence to an already existing equivalent fact.
    linked_existing: Mapped[bool] = mapped_column(default=False)

    ingestion: Mapped[DocumentIngestion] = relationship(back_populates="proposals")
