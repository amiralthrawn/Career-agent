"""Application lifecycle tracking (step 11): a reliable, append-only history of what actually
happened to a candidature, and the substrate a later, SEPARATE learning step would need - never
a score, never an automatic promotion of a guess into a fact.

**`PREPARED`/`APPROVED`/`SENT` are recorded automatically**, by `ApplicationPackageService`/
`SendBatchService` themselves (`record_system_event`, called from those existing, otherwise
unmodified services) the moment they already know it for certain - a human can never manually
claim "sent" (Career-agent's own send mechanism is the only authority for that fact; see
`_SYSTEM_ONLY_TYPES`).

**Every other event starts `UNCERTAIN` unless a human directly asserts it.** A human's own
`record_manual_event` call is trusted as `FOUND` by default (they are reporting their own inbox);
a (future) Gmail-detected `ReplyEvidence` is always recorded as `UNCERTAIN`
(`record_detected_events`) until a human explicitly confirms or corrects it
(`correct_event`) - which creates a NEW row, never edits the old one.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.integrations.gmail.reply_detection import ReplyEvidence
from app.models import ApplicationEvent, ApplicationPackage
from app.models.enums import ApplicationEventOrigin, ApplicationEventType, InfoStatus
from app.repositories import application_event as repo
from app.repositories import candidate_brain as brain_repo
from app.repositories.application_package import get_package

# Only the existing step 9/10 services may record these - never a human, never a Gmail guess:
# "sent" in particular must stay backed by a real SendBatchItem/SentMessage, not a claim.
_SYSTEM_ONLY_TYPES = frozenset(
    {ApplicationEventType.PREPARED, ApplicationEventType.APPROVED, ApplicationEventType.SENT}
)

DEFAULT_FOLLOW_UP_DAYS = 14


def record_system_event(
    session: Session,
    candidate_id: int,
    application_package_id: int,
    event_type: ApplicationEventType,
    *,
    reference: str | None = None,
    occurred_at: datetime | None = None,
    actor: str = "system",
) -> ApplicationEvent:
    """Called directly from `ApplicationPackageService`/`SendBatchService` - never from the API.
    Always `origin=SYSTEM`, `status=FOUND`: these three facts are certain the moment they happen.
    """
    assert event_type in _SYSTEM_ONLY_TYPES
    return repo.stage(
        session,
        ApplicationEvent(
            candidate_id=candidate_id,
            application_package_id=application_package_id,
            event_type=event_type,
            origin=ApplicationEventOrigin.SYSTEM,
            status=InfoStatus.FOUND,
            occurred_at=occurred_at or datetime.now(UTC),
            reference=reference,
            actor=actor,
        ),
    )


class ApplicationTrackingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    def _package(self, package_id: int) -> ApplicationPackage:
        package = get_package(self._session, self._candidate_id(), package_id)
        if package is None:
            raise NotFoundError(f"Application package {package_id} not found")
        return package

    # --- read -------------------------------------------------------------------------

    def list_events(self, package_id: int) -> Sequence[ApplicationEvent]:
        self._package(package_id)  # ownership check
        return repo.list_events(self._session, package_id)

    def get_event(self, event_id: int) -> ApplicationEvent:
        event = repo.get_event(self._session, self._candidate_id(), event_id)
        if event is None:
            raise NotFoundError(f"Application event {event_id} not found")
        return event

    def list_packages(
        self,
        *,
        event_type: ApplicationEventType | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[ApplicationPackage]:
        return repo.list_packages_by_event(
            self._session, self._candidate_id(), event_type=event_type, since=since, until=until
        )

    def needing_follow_up(
        self, *, days_since_sent: int = DEFAULT_FOLLOW_UP_DAYS
    ) -> Sequence[ApplicationPackage]:
        cutoff = datetime.now(UTC) - timedelta(days=days_since_sent)
        return repo.list_needing_follow_up(self._session, self._candidate_id(), sent_before=cutoff)

    def list_all_events(
        self, *, target_id: int | None = None, package_id: int | None = None, limit: int = 50
    ) -> Sequence[ApplicationEvent]:
        """Every event, optionally narrowed to one target's package(s) or one package - the
        operational journal (`career-agent events`), never a second event store."""
        return repo.list_all(
            self._session,
            self._candidate_id(),
            target_id=target_id,
            package_id=package_id,
            limit=limit,
        )

    # --- manual entry -------------------------------------------------------------------

    def record_manual_event(
        self,
        package_id: int,
        event_type: ApplicationEventType,
        occurred_at: datetime,
        *,
        status: InfoStatus = InfoStatus.FOUND,
        note: str | None = None,
        reference: str | None = None,
        actor: str = "api",
    ) -> ApplicationEvent:
        if event_type in _SYSTEM_ONLY_TYPES:
            raise UnprocessableError(
                f"'{event_type.value}' is recorded automatically and cannot be entered manually"
            )
        package = self._package(package_id)
        if reference is not None:
            existing = repo.existing_by_reference(self._session, package.id, event_type, reference)
            if existing is not None:
                return existing  # idempotent: the same (type, reference) is never duplicated
        event = repo.stage(
            self._session,
            ApplicationEvent(
                candidate_id=package.candidate_id,
                application_package_id=package.id,
                event_type=event_type,
                origin=ApplicationEventOrigin.MANUAL,
                status=status,
                occurred_at=occurred_at,
                reference=reference,
                note=note,
                actor=actor,
            ),
        )
        repo.commit(self._session)
        self._session.refresh(event)
        return event

    # --- detected (Gmail preparation - fed by a fake in tests; no real source exists yet) -----

    def record_detected_events(
        self,
        package_id: int,
        evidence: Sequence[ReplyEvidence],
        *,
        actor: str = "system",
    ) -> list[ApplicationEvent]:
        """Always `UNCERTAIN`: a detection is a lead for a human to review, never a fact."""
        package = self._package(package_id)
        created: list[ApplicationEvent] = []
        for item in evidence:
            event_type = item.suggested_type or ApplicationEventType.RESPONSE_RECEIVED
            existing = repo.existing_by_reference(
                self._session, package.id, event_type, item.message_id
            )
            if existing is not None:
                continue  # idempotent: the same Gmail message is never recorded twice
            created.append(
                repo.stage(
                    self._session,
                    ApplicationEvent(
                        candidate_id=package.candidate_id,
                        application_package_id=package.id,
                        event_type=event_type,
                        origin=ApplicationEventOrigin.GMAIL,
                        status=InfoStatus.UNCERTAIN,
                        occurred_at=item.received_at,
                        reference=item.message_id,
                        note=item.excerpt,
                        actor=actor,
                    ),
                )
            )
        repo.commit(self._session)
        for event in created:
            self._session.refresh(event)
        return created

    # --- correction (also how an UNCERTAIN event gets confirmed) -----------------------------

    def correct_event(
        self,
        event_id: int,
        *,
        event_type: ApplicationEventType | None = None,
        occurred_at: datetime | None = None,
        status: InfoStatus | None = None,
        note: str | None = None,
        reference: str | None = None,
        actor: str = "api",
    ) -> ApplicationEvent:
        """A correction is a NEW row: the original is never edited or deleted, so the full
        history - including the mistake - stays reconstructible. Confirming an `UNCERTAIN`
        event is just a correction that only changes `status` to `FOUND`."""
        original = self.get_event(event_id)
        new_type = event_type if event_type is not None else original.event_type
        if new_type in _SYSTEM_ONLY_TYPES and original.origin is not ApplicationEventOrigin.SYSTEM:
            raise UnprocessableError(f"'{new_type.value}' can only ever be a system-recorded fact")
        event = repo.stage(
            self._session,
            ApplicationEvent(
                candidate_id=original.candidate_id,
                application_package_id=original.application_package_id,
                event_type=new_type,
                origin=ApplicationEventOrigin.MANUAL,
                status=status if status is not None else original.status,
                occurred_at=occurred_at if occurred_at is not None else original.occurred_at,
                reference=reference if reference is not None else original.reference,
                note=note if note is not None else original.note,
                corrected_event_id=original.id,
                actor=actor,
            ),
        )
        repo.commit(self._session)
        self._session.refresh(event)
        return event
