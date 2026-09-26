"""Data access for the audit trail. Deliberately read/append only: no update, no delete."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent, AuditEventType


def append(session: Session, event: AuditEvent) -> AuditEvent:
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def list_events(
    session: Session, *, event_type: AuditEventType | None = None, limit: int = 100
) -> Sequence[AuditEvent]:
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if event_type is not None:
        statement = statement.where(AuditEvent.event_type == event_type)
    return session.scalars(statement).all()
