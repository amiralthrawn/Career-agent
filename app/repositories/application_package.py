"""Data access for application packages (step 9). No commit here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationPackage, DocumentIngestion
from app.models.enums import ApplicationPackageStatus, SendBatchItemStatus
from app.models.send_batch import SendBatchItem

PENDING_STATUSES = (ApplicationPackageStatus.DRAFT, ApplicationPackageStatus.PENDING_VALIDATION)


def get_package(session: Session, candidate_id: int, package_id: int) -> ApplicationPackage | None:
    return session.scalars(
        select(ApplicationPackage).where(
            ApplicationPackage.id == package_id, ApplicationPackage.candidate_id == candidate_id
        )
    ).first()


def find_pending(session: Session, target_id: int) -> ApplicationPackage | None:
    return session.scalars(
        select(ApplicationPackage).where(
            ApplicationPackage.target_id == target_id,
            ApplicationPackage.status.in_(PENDING_STATUSES),
        )
    ).first()


def latest_cv_ingestion(session: Session, candidate_id: int) -> DocumentIngestion | None:
    """The most recently ingested CV document, if any - never modified, only referenced."""
    return session.scalars(
        select(DocumentIngestion)
        .where(DocumentIngestion.candidate_id == candidate_id)
        .order_by(DocumentIngestion.id.desc())
    ).first()


def list_ready_to_send(session: Session, candidate_id: int) -> Sequence[ApplicationPackage]:
    """Individually-approved packages (step 9) never yet sent (step 10, in any batch) - what a
    human picks from to build a `SendBatch`."""
    already_sent = select(SendBatchItem.application_package_id).where(
        SendBatchItem.status == SendBatchItemStatus.SENT
    )
    return session.scalars(
        select(ApplicationPackage)
        .where(
            ApplicationPackage.candidate_id == candidate_id,
            ApplicationPackage.status == ApplicationPackageStatus.APPROVED,
            ApplicationPackage.id.not_in(already_sent),
        )
        .order_by(ApplicationPackage.id)
    ).all()
