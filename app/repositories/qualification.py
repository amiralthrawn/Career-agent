"""Data access for search profiles, criteria and qualifications. No commit here."""

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Qualification, SearchCriterion, SearchProfile, Target
from app.models.enums import TargetStatus


def get_profile(session: Session, candidate_id: int, profile_id: int) -> SearchProfile | None:
    return session.scalars(
        select(SearchProfile).where(
            SearchProfile.id == profile_id, SearchProfile.candidate_id == candidate_id
        )
    ).first()


def active_profile(session: Session, candidate_id: int) -> SearchProfile | None:
    return session.scalars(
        select(SearchProfile).where(
            SearchProfile.candidate_id == candidate_id, SearchProfile.is_active
        )
    ).first()


def list_profiles(session: Session, candidate_id: int) -> Sequence[SearchProfile]:
    return session.scalars(
        select(SearchProfile)
        .where(SearchProfile.candidate_id == candidate_id)
        .order_by(SearchProfile.id)
    ).all()


def deactivate_profiles(session: Session, candidate_id: int) -> None:
    session.execute(
        update(SearchProfile)
        .where(SearchProfile.candidate_id == candidate_id, SearchProfile.is_active)
        .values(is_active=False)
    )


def get_criterion(session: Session, profile_id: int, criterion_id: int) -> SearchCriterion | None:
    return session.scalars(
        select(SearchCriterion).where(
            SearchCriterion.id == criterion_id, SearchCriterion.profile_id == profile_id
        )
    ).first()


def latest_qualification(session: Session, target_id: int, profile_id: int) -> Qualification | None:
    return session.scalars(
        select(Qualification)
        .where(Qualification.target_id == target_id, Qualification.profile_id == profile_id)
        .order_by(Qualification.id.desc())
        .limit(1)
    ).first()


def latest_for_profile(
    session: Session, candidate_id: int, profile_id: int
) -> Sequence[Qualification]:
    """The latest qualification per target, for one profile - at most one row per target.

    Used to list targets by their CURRENT decision (step 3a's `qualification opportunities
    qualified/uncertain`) without re-running any evaluation: a target that was never qualified
    against this profile is simply absent, never reported with a guessed status.
    """
    latest_ids = (
        select(func.max(Qualification.id))
        .where(Qualification.candidate_id == candidate_id, Qualification.profile_id == profile_id)
        .group_by(Qualification.target_id)
    )
    return session.scalars(
        select(Qualification)
        .where(Qualification.id.in_(latest_ids))
        .order_by(Qualification.target_id)
    ).all()


def targets_to_qualify(session: Session, candidate_id: int) -> Sequence[Target]:
    """Every target except those the human dismissed."""
    return session.scalars(
        select(Target)
        .where(Target.candidate_id == candidate_id, Target.status != TargetStatus.DISMISSED)
        .order_by(Target.id)
    ).all()
