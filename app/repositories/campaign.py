"""Data access for campaigns. No commit here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Campaign, SearchRun, SearchRunItem


def get_campaign(session: Session, candidate_id: int, campaign_id: int) -> Campaign | None:
    return session.scalars(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.candidate_id == candidate_id)
    ).first()


def list_campaigns(
    session: Session, candidate_id: int, *, limit: int, offset: int
) -> Sequence[Campaign]:
    return session.scalars(
        select(Campaign)
        .where(Campaign.candidate_id == candidate_id)
        .order_by(Campaign.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()


def target_ids_for_campaign(session: Session, campaign_id: int) -> list[int]:
    """Every distinct target this campaign's rounds produced (created or matched an existing
    one) - the scope a funnel view is computed over."""
    rows = session.scalars(
        select(SearchRunItem.target_id)
        .join(SearchRun, SearchRunItem.run_id == SearchRun.id)
        .where(SearchRun.campaign_id == campaign_id, SearchRunItem.target_id.is_not(None))
        .distinct()
    ).all()
    return [target_id for target_id in rows if target_id is not None]
