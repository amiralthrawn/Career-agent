"""Data access for document ingestions and their proposals."""

from collections.abc import Sequence

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import DocumentIngestion, IngestionProposal
from app.models.base import Base
from app.models.enums import EvidenceTargetType, ProposalStatus


def get_ingestion_by_hash(
    session: Session, candidate_id: int, sha256: str
) -> DocumentIngestion | None:
    statement = select(DocumentIngestion).where(
        DocumentIngestion.candidate_id == candidate_id, DocumentIngestion.sha256 == sha256
    )
    return session.scalars(statement).first()


def list_proposals(
    session: Session,
    candidate_id: int,
    *,
    status: ProposalStatus | None = None,
    kind: EvidenceTargetType | None = None,
    ingestion_id: int | None = None,
) -> Sequence[IngestionProposal]:
    statement = select(IngestionProposal).where(IngestionProposal.candidate_id == candidate_id)
    if status is not None:
        statement = statement.where(IngestionProposal.status == status)
    if kind is not None:
        statement = statement.where(IngestionProposal.kind == kind)
    if ingestion_id is not None:
        statement = statement.where(IngestionProposal.ingestion_id == ingestion_id)
    return session.scalars(statement.order_by(IngestionProposal.id)).all()


def claim_pending(session: Session, proposal_id: int, new_status: ProposalStatus) -> bool:
    """Atomically move a proposal out of `pending`. False if it was already decided.

    The UPDATE is conditional, so two concurrent decisions cannot both succeed.
    """
    result = session.execute(
        update(IngestionProposal)
        .where(
            IngestionProposal.id == proposal_id, IngestionProposal.status == ProposalStatus.PENDING
        )
        .values(status=new_status)
        .execution_options(synchronize_session="fetch")
    )
    return isinstance(result, CursorResult) and result.rowcount == 1


def stage(session: Session, instance: Base) -> None:
    """Add and flush without committing (the caller owns the transaction)."""
    session.add(instance)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error


def commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error
