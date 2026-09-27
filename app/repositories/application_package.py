"""Data access for application packages (step 9). No commit here."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationPackage, DocumentIngestion
from app.models.enums import ApplicationPackageStatus

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
