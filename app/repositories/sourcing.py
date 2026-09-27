"""Data access for sourcing runs. No commit here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SearchRun, SearchRunItem


def get_run(session: Session, candidate_id: int, run_id: int) -> SearchRun | None:
    return session.scalars(
        select(SearchRun).where(SearchRun.id == run_id, SearchRun.candidate_id == candidate_id)
    ).first()


def list_runs(
    session: Session, candidate_id: int, *, profile_id: int | None, limit: int, offset: int
) -> Sequence[SearchRun]:
    statement = select(SearchRun).where(SearchRun.candidate_id == candidate_id)
    if profile_id is not None:
        statement = statement.where(SearchRun.profile_id == profile_id)
    return session.scalars(
        statement.order_by(SearchRun.id.desc()).limit(limit).offset(offset)
    ).all()


def list_items(session: Session, run_id: int) -> Sequence[SearchRunItem]:
    return session.scalars(
        select(SearchRunItem).where(SearchRunItem.run_id == run_id).order_by(SearchRunItem.position)
    ).all()
