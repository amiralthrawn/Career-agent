"""Data access for application drafts. No commit here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationDraft
from app.models.enums import DraftKind, DraftStatus


def list_drafts(
    session: Session,
    candidate_id: int,
    *,
    status: DraftStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ApplicationDraft]:
    statement = select(ApplicationDraft).where(ApplicationDraft.candidate_id == candidate_id)
    if status is not None:
        statement = statement.where(ApplicationDraft.status == status)
    statement = statement.order_by(ApplicationDraft.id.desc()).limit(limit).offset(offset)
    return session.scalars(statement).all()


def get_draft(session: Session, candidate_id: int, draft_id: int) -> ApplicationDraft | None:
    return session.scalars(
        select(ApplicationDraft).where(
            ApplicationDraft.id == draft_id, ApplicationDraft.candidate_id == candidate_id
        )
    ).first()


def find_pending(session: Session, target_id: int, kind: DraftKind) -> ApplicationDraft | None:
    return session.scalars(
        select(ApplicationDraft).where(
            ApplicationDraft.target_id == target_id,
            ApplicationDraft.kind == kind,
            ApplicationDraft.status == DraftStatus.PROPOSED,
        )
    ).first()
