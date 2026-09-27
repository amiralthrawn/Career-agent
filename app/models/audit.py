"""Append-only audit trail: what the application attempted or authorised.

It is deliberately NOT a general log. There is no free-form text: event types are a closed
set, `details` only accepts a small set of scalar keys (see `app.services.audit`), so mail
content, attachments, secrets, the API token and profile data cannot end up here.

Immutability is enforced at three levels: the repository only offers `add`, ORM updates and
deletes raise, and database triggers reject UPDATE, DELETE (and TRUNCATE on PostgreSQL).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DDL, JSON, DateTime, String, event, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base
from app.models.enums import enum_column


class AuditEventType(StrEnum):
    APP_STARTED = "app.started"
    SECRET_SET = "secret.set"
    SECRET_DELETED = "secret.deleted"
    SEND_BLOCKED = "send.blocked"
    SEND_DRY_RUN = "send.dry_run"
    SEND_APPROVED = "send.approved"
    SEND_SENT = "send.sent"
    SEND_FAILED = "send.failed"
    IMPORT_APPLIED = "import.applied"
    QUALIFICATION_RUN = "qualification.run"
    REQUIREMENTS_EXTRACTED = "requirements.extract"
    SOURCING_RUN = "sourcing.run"
    DRAFT_GENERATED = "draft.generated"
    DRAFT_DECIDED = "draft.decided"
    RESEARCH_BATCH_RUN = "research_batch.run"
    CONTACT_RESEARCH_BATCH_RUN = "contact_research_batch.run"
    APPLICATION_PREPARED = "application.prepared"
    APPLICATION_DECIDED = "application.decided"
    SEND_BATCH_CREATED = "send_batch.created"
    SEND_BATCH_APPROVED = "send_batch.approved"
    SEND_REQUESTED = "send_batch.send_requested"
    SEND_BATCH_COMPLETED = "send_batch.completed"
    SEND_BATCH_PARTIALLY_FAILED = "send_batch.partially_failed"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(enum_column(AuditEventType), index=True)
    # Who triggered it: "system", "cli" or "api".
    actor: Mapped[str] = mapped_column(String(16))
    # What it is about, e.g. "secret:gmail_refresh_token" (never personal data).
    subject: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# --- Database-level immutability --------------------------------------------------------

SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
)
POSTGRESQL_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql"
)
POSTGRESQL_TRIGGERS = (
    "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable()",
)


def _install_after_create(statement: str, dialect: str) -> None:
    ddl = DDL(statement).execute_if(dialect=dialect)  # type: ignore[no-untyped-call]
    event.listen(AuditEvent.__table__, "after_create", ddl)


for _statement in SQLITE_TRIGGERS:
    _install_after_create(_statement, "sqlite")
_install_after_create(POSTGRESQL_FUNCTION, "postgresql")
for _statement in POSTGRESQL_TRIGGERS:
    _install_after_create(_statement, "postgresql")


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def _refuse_change(mapper: Mapper[Any], connection: Any, target: AuditEvent) -> None:
    raise RuntimeError("audit_events is append-only")
