"""Data access for application drafts. No commit here."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationDraft
from app.models.enums import DraftKind, DraftStatus


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
