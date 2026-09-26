"""Data access for the Candidate Brain. No business rules here: only queries and persistence."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import Base, Candidate
from app.models.base import CandidateOwnedMixin


def get_first_candidate(session: Session) -> Candidate | None:
    return session.scalars(select(Candidate).order_by(Candidate.id).limit(1)).first()


def add[T: Base](session: Session, instance: T) -> T:
    """Persist `instance`, translating integrity violations into `ConflictError`."""
    session.add(instance)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error
    session.refresh(instance)
    return instance


def list_for_candidate[T: CandidateOwnedMixin](
    session: Session, model: type[T], candidate_id: int
) -> Sequence[T]:
    statement = select(model).where(model.candidate_id == candidate_id).order_by(model.id)
    return session.scalars(statement).all()


def get_for_candidate[T: CandidateOwnedMixin](
    session: Session, model: type[T], candidate_id: int, object_id: int
) -> T | None:
    statement = select(model).where(model.id == object_id, model.candidate_id == candidate_id)
    return session.scalars(statement).first()
