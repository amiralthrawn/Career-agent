"""Data access for target requirements. No commit here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TargetRequirement
from app.models.enums import RequirementKind


def list_for_target(
    session: Session, target_id: int, *, include_inactive: bool = False
) -> Sequence[TargetRequirement]:
    statement = select(TargetRequirement).where(TargetRequirement.target_id == target_id)
    if not include_inactive:
        statement = statement.where(TargetRequirement.active)
    return session.scalars(statement.order_by(TargetRequirement.id)).all()


def find_active(
    session: Session, target_id: int, kind: RequirementKind, key: str
) -> TargetRequirement | None:
    return session.scalars(
        select(TargetRequirement).where(
            TargetRequirement.target_id == target_id,
            TargetRequirement.kind == kind,
            TargetRequirement.key == key,
            TargetRequirement.active,
        )
    ).first()
