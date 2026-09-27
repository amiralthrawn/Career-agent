"""Data access for send batches (step 10). No commit here (except `commit`, matching the
`app.repositories.ingestion` convention).

Every state transition that must not race (two concurrent approvals, two concurrent executions,
two concurrent attempts on the same item) is a single conditional `UPDATE ... WHERE status = ...`,
the same pattern already proven for `IngestionProposal`/`ContactResearchObservation` - never a
read-then-write from Python, which a second request could interleave with.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import CursorResult, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import SendBatch, SendBatchItem
from app.models.base import Base
from app.models.enums import SendBatchItemStatus, SendBatchStatus

RESUMABLE_BATCH_STATUSES = (SendBatchStatus.APPROVED, SendBatchStatus.PARTIALLY_FAILED)
RETRYABLE_ITEM_STATUSES = (SendBatchItemStatus.PENDING, SendBatchItemStatus.FAILED)


def stage[T: Base](session: Session, instance: T) -> T:
    session.add(instance)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error
    return instance


def commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error


def get_batch(session: Session, candidate_id: int, batch_id: int) -> SendBatch | None:
    return session.scalars(
        select(SendBatch).where(SendBatch.id == batch_id, SendBatch.candidate_id == candidate_id)
    ).first()


def get_batch_by_idempotency_key(session: Session, candidate_id: int, key: str) -> SendBatch | None:
    return session.scalars(
        select(SendBatch).where(
            SendBatch.candidate_id == candidate_id, SendBatch.idempotency_key == key
        )
    ).first()


def list_items(session: Session, batch_id: int) -> Sequence[SendBatchItem]:
    return session.scalars(
        select(SendBatchItem).where(SendBatchItem.batch_id == batch_id).order_by(SendBatchItem.id)
    ).all()


def get_item(session: Session, item_id: int) -> SendBatchItem | None:
    return session.get(SendBatchItem, item_id)


def has_sent_item_for_package(session: Session, application_package_id: int) -> bool:
    """The core idempotence check: was this package EVER sent, in any batch, ever."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    SendBatchItem.application_package_id == application_package_id,
                    SendBatchItem.status == SendBatchItemStatus.SENT,
                )
            )
        )
    )


def approve_batch(session: Session, batch_id: int, *, approved_by: str, now: datetime) -> bool:
    """`draft` -> `approved`, atomically. False if it was not `draft` (already approved, or
    never existed as a batch this call can see - the caller checks existence separately)."""
    result = session.execute(
        update(SendBatch)
        .where(SendBatch.id == batch_id, SendBatch.status == SendBatchStatus.DRAFT)
        .values(status=SendBatchStatus.APPROVED, approved_at=now, approved_by=approved_by)
        .execution_options(synchronize_session="fetch")
    )
    return isinstance(result, CursorResult) and result.rowcount == 1


def claim_batch_executing(session: Session, batch_id: int) -> bool:
    """`approved`/`partially_failed` -> `executing`, atomically. False if a concurrent call
    already claimed it, or it is not in a resumable state."""
    result = session.execute(
        update(SendBatch)
        .where(SendBatch.id == batch_id, SendBatch.status.in_(RESUMABLE_BATCH_STATUSES))
        .values(status=SendBatchStatus.EXECUTING)
        .execution_options(synchronize_session="fetch")
    )
    return isinstance(result, CursorResult) and result.rowcount == 1


def claim_item_sending(session: Session, item_id: int) -> bool:
    """`pending`/`failed` -> `sending`, atomically. False if already claimed or terminal."""
    result = session.execute(
        update(SendBatchItem)
        .where(SendBatchItem.id == item_id, SendBatchItem.status.in_(RETRYABLE_ITEM_STATUSES))
        .values(status=SendBatchItemStatus.SENDING)
        .execution_options(synchronize_session="fetch")
    )
    return isinstance(result, CursorResult) and result.rowcount == 1
