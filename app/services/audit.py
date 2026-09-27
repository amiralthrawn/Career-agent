"""Audit trail service.

The audit answers "what did the application attempt or authorise?" and nothing else. It is
built so that sensitive data cannot be stored by mistake:

- event types are a closed set (`AuditEventType`);
- `details` only accepts whitelisted keys with short scalar values;
- a value that looks like an e-mail address, contains a line break, or is too long is refused.

Mail content, attachments, secrets, the API token and profile data never belong here. Only the
recipient's *domain* may be recorded.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent, AuditEventType
from app.repositories import audit as repo

# Whitelist of detail keys. Adding one is a deliberate decision, made in code review.
ALLOWED_DETAIL_KEYS = frozenset(
    {
        "reason",  # short machine-readable code, e.g. "recipient_not_allowed"
        "mode",  # send mode
        "recipient_domain",  # domain only, never the address
        "attachments",  # count
        "bytes",  # size
        "name",  # a secret's NAME (never its value)
        "app_version",
        "rows",  # import counters (numbers only)
        "created",
        "matched",
        "rejected",
        "model",  # the LLM model identifier used (never a key or a prompt)
        "correlation_id",  # a SendBatch's own id, to reconstruct one batch's full history
        "batch_id",
        "package_id",  # an ApplicationPackage's id (never its content)
    }
)
ALLOWED_ACTORS = frozenset({"system", "cli", "api"})
MAX_VALUE_CHARS = 64

DetailValue = str | int | bool | None


class AuditRejectedError(ValueError):
    """The event was refused because it could carry sensitive data."""


def _check_details(details: Mapping[str, DetailValue]) -> dict[str, DetailValue]:
    unknown = sorted(set(details) - ALLOWED_DETAIL_KEYS)
    if unknown:
        raise AuditRejectedError(f"Audit detail keys not allowed: {', '.join(unknown)}")
    for key, value in details.items():
        if isinstance(value, str) and (
            len(value) > MAX_VALUE_CHARS or "@" in value or "\n" in value or "\r" in value
        ):
            raise AuditRejectedError(f"Audit detail '{key}' is not an acceptable short value")
        if not isinstance(value, str | int | bool | None):
            raise AuditRejectedError(f"Audit detail '{key}' must be a scalar")
    return dict(details)


class AuditLog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        event_type: AuditEventType,
        *,
        actor: str,
        subject: str | None = None,
        details: Mapping[str, DetailValue] | None = None,
    ) -> AuditEvent:
        if actor not in ALLOWED_ACTORS:
            raise AuditRejectedError("Unknown audit actor")
        if subject is not None and (len(subject) > MAX_VALUE_CHARS or "@" in subject):
            raise AuditRejectedError("Audit subject is not an acceptable short value")
        event = AuditEvent(
            event_type=event_type,
            actor=actor,
            subject=subject,
            details=_check_details(details or {}),
        )
        return repo.append(self._session, event)

    def recent(
        self, *, event_type: AuditEventType | None = None, limit: int = 100
    ) -> Sequence[AuditEvent]:
        return repo.list_events(self._session, event_type=event_type, limit=limit)
