"""Data access for application lifecycle events (step 11). No commit here."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import ApplicationEvent, ApplicationPackage
from app.models.base import Base
from app.models.enums import ApplicationEventType, InfoStatus

# Event types that mean "the candidature is no longer waiting for a first response" - a package
# with one of these, confirmed, after its `sent` event is never proposed for follow-up.
RESPONSE_TYPES = (
    ApplicationEventType.RESPONSE_RECEIVED,
    ApplicationEventType.INTERVIEW_PROPOSED,
    ApplicationEventType.INTERVIEW_SCHEDULED,
    ApplicationEventType.INTERVIEW_COMPLETED,
    ApplicationEventType.REJECTED,
    ApplicationEventType.OFFER_RECEIVED,
    ApplicationEventType.WITHDRAWN,
)


def stage[T: Base](session: Session, instance: T) -> T:
    session.add(instance)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error
    return instance


def commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ConflictError("The record conflicts with an existing one") from error


def list_events(session: Session, application_package_id: int) -> Sequence[ApplicationEvent]:
    return session.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_package_id == application_package_id)
        .order_by(ApplicationEvent.occurred_at, ApplicationEvent.id)
    ).all()


def get_event(session: Session, candidate_id: int, event_id: int) -> ApplicationEvent | None:
    return session.scalars(
        select(ApplicationEvent).where(
            ApplicationEvent.id == event_id, ApplicationEvent.candidate_id == candidate_id
        )
    ).first()


def existing_by_reference(
    session: Session,
    application_package_id: int,
    event_type: ApplicationEventType,
    reference: str,
) -> ApplicationEvent | None:
    """Mirrors the unique index (`application_package_id`, `event_type`, `reference`): lets a
    caller give a clean, expected outcome instead of catching an `IntegrityError`."""
    return session.scalars(
        select(ApplicationEvent).where(
            ApplicationEvent.application_package_id == application_package_id,
            ApplicationEvent.event_type == event_type,
            ApplicationEvent.reference == reference,
        )
    ).first()


def list_packages_by_event(
    session: Session,
    candidate_id: int,
    *,
    event_type: ApplicationEventType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Sequence[ApplicationPackage]:
    """No `event_type`: every package, optionally bounded by its OWN creation date. With
    `event_type`: only packages with a CONFIRMED event of that type in `[since, until]` - a
    date range here bounds when the real-world fact happened, not when the package was made."""
    if event_type is None:
        statement = select(ApplicationPackage).where(
            ApplicationPackage.candidate_id == candidate_id
        )
        if since is not None:
            statement = statement.where(ApplicationPackage.created_at >= since)
        if until is not None:
            statement = statement.where(ApplicationPackage.created_at <= until)
        return session.scalars(statement.order_by(ApplicationPackage.id)).all()

    conditions = [
        ApplicationEvent.application_package_id == ApplicationPackage.id,
        ApplicationEvent.event_type == event_type,
        ApplicationEvent.status == InfoStatus.FOUND,
    ]
    if since is not None:
        conditions.append(ApplicationEvent.occurred_at >= since)
    if until is not None:
        conditions.append(ApplicationEvent.occurred_at <= until)
    statement = select(ApplicationPackage).where(
        ApplicationPackage.candidate_id == candidate_id, exists().where(*conditions)
    )
    return session.scalars(statement.order_by(ApplicationPackage.id)).all()


def list_all(
    session: Session,
    candidate_id: int,
    *,
    target_id: int | None = None,
    package_id: int | None = None,
    limit: int = 50,
) -> Sequence[ApplicationEvent]:
    """Every event across every package, newest first - optionally narrowed to one target's
    package(s) or one package. Read-only; not a second event store, just a wider query over the
    same `ApplicationEvent` rows `list_events` already reads one package at a time."""
    statement = (
        select(ApplicationEvent)
        .join(ApplicationPackage, ApplicationEvent.application_package_id == ApplicationPackage.id)
        .where(ApplicationPackage.candidate_id == candidate_id)
    )
    if target_id is not None:
        statement = statement.where(ApplicationPackage.target_id == target_id)
    if package_id is not None:
        statement = statement.where(ApplicationPackage.id == package_id)
    statement = statement.order_by(
        ApplicationEvent.recorded_at.desc(), ApplicationEvent.id.desc()
    ).limit(limit)
    return session.scalars(statement).all()


def list_needing_follow_up(
    session: Session, candidate_id: int, *, sent_before: datetime
) -> Sequence[ApplicationPackage]:
    """Packages `sent` (confirmed) before `sent_before`, with no confirmed response since."""
    sent = (
        select(
            ApplicationEvent.application_package_id,
            func.max(ApplicationEvent.occurred_at).label("sent_at"),
        )
        .where(
            ApplicationEvent.event_type == ApplicationEventType.SENT,
            ApplicationEvent.status == InfoStatus.FOUND,
        )
        .group_by(ApplicationEvent.application_package_id)
        .subquery()
    )
    has_response = exists().where(
        ApplicationEvent.application_package_id == sent.c.application_package_id,
        ApplicationEvent.event_type.in_(RESPONSE_TYPES),
        ApplicationEvent.status == InfoStatus.FOUND,
    )
    statement = (
        select(ApplicationPackage)
        .join(sent, sent.c.application_package_id == ApplicationPackage.id)
        .where(
            ApplicationPackage.candidate_id == candidate_id,
            sent.c.sent_at <= sent_before,
            ~has_response,
        )
        .order_by(sent.c.sent_at)
    )
    return session.scalars(statement).all()
