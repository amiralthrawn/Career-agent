"""Controlled batch sending (step 10): group already-approved `ApplicationPackage`s, get one
explicit batch-level human approval, then send - synchronously, within one call, no background
worker, no cron. See `app.models.send_batch` for the two-layer approval model this reuses for
both a single send (a batch of one) and a real batch (many).

**Every precondition is re-checked at send time, never trusted from an earlier snapshot.**
`_preflight` re-validates staleness, the accepted contact, `do_not_contact`, the CV reference and
"not already sent elsewhere" for EACH item, right before it is attempted - the individual and
batch approvals establish INTENT, not a fact that stays true forever.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import make_msgid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.mail.ports import (
    AttachmentRef,
    IdempotentMailProvider,
    OutgoingEmail,
    SentMessage,
    recipient_domain,
)
from app.models import ApplicationDraft, ApplicationPackage, Contact, DocumentIngestion, SendBatch
from app.models.audit import AuditEventType
from app.models.enums import ApplicationPackageStatus, SendBatchItemStatus, SendBatchStatus
from app.models.send_batch import SendBatchItem
from app.repositories import candidate_brain as brain_repo
from app.repositories import send_batch as repo
from app.services.application_package import ApplicationPackageService, best_email_channel
from app.services.audit import AuditLog
from app.services.mail_sender import MailSender


def _correlation_id() -> str:
    return uuid.uuid4().hex


class SendBatchService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        mail_sender: MailSender,
        packages: ApplicationPackageService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._mail_sender = mail_sender
        self._packages = packages

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- creation ---------------------------------------------------------------------

    def create(
        self,
        application_package_ids: Sequence[int],
        *,
        idempotency_key: str | None = None,
        actor: str = "api",
    ) -> SendBatch:
        candidate_id = self._candidate_id()
        if idempotency_key:
            existing = repo.get_batch_by_idempotency_key(
                self._session, candidate_id, idempotency_key
            )
            if existing is not None:
                return existing  # batch-creation idempotence: the same key returns the same batch

        if not application_package_ids:
            raise UnprocessableError("At least one application package is required")
        if len(set(application_package_ids)) != len(application_package_ids):
            raise UnprocessableError("application_package_ids must not repeat")

        packages = []
        for package_id in application_package_ids:
            package = self._packages.get(package_id)  # 404 if unknown/not this candidate's
            if package.status is not ApplicationPackageStatus.APPROVED:
                raise UnprocessableError(
                    f"Application package {package_id} is not approved (layer 1: individual "
                    "review) and cannot be added to a batch"
                )
            if repo.has_sent_item_for_package(self._session, package_id):
                raise UnprocessableError(f"Application package {package_id} was already sent")
            packages.append(package)

        batch = repo.stage(
            self._session,
            SendBatch(
                candidate_id=candidate_id,
                status=SendBatchStatus.DRAFT,
                correlation_id=_correlation_id(),
                idempotency_key=idempotency_key,
                created_by=actor,
            ),
        )
        self._session.flush()
        for package in packages:
            repo.stage(
                self._session,
                SendBatchItem(
                    batch_id=batch.id,
                    application_package_id=package.id,
                    status=SendBatchItemStatus.PENDING,
                ),
            )
        AuditLog(self._session).record(
            AuditEventType.SEND_BATCH_CREATED,
            actor=actor,
            subject=f"batch:{batch.id}",
            details={"correlation_id": batch.correlation_id, "rows": len(packages)},
        )
        repo.commit(self._session)
        self._session.refresh(batch)
        return batch

    def get(self, batch_id: int) -> SendBatch:
        batch = repo.get_batch(self._session, self._candidate_id(), batch_id)
        if batch is None:
            raise NotFoundError(f"Send batch {batch_id} not found")
        return batch

    def items(self, batch_id: int) -> Sequence[SendBatchItem]:
        self.get(batch_id)  # ownership check
        return repo.list_items(self._session, batch_id)

    # --- approval (layer 2, on top of each package's own layer-1 approval) -----------------

    def approve(self, batch_id: int, *, actor: str = "api") -> SendBatch:
        batch = self.get(batch_id)
        now = datetime.now(UTC)
        if not repo.approve_batch(self._session, batch_id, approved_by=actor, now=now):
            raise ConflictError(f"The batch has already been decided ({batch.status.value})")
        AuditLog(self._session).record(
            AuditEventType.SEND_BATCH_APPROVED,
            actor=actor,
            subject=f"batch:{batch_id}",
            details={"correlation_id": batch.correlation_id},
        )
        repo.commit(self._session)
        self._session.refresh(batch)
        return batch

    # --- execution: the one explicit action that actually sends anything -------------------

    def execute(self, batch_id: int, *, actor: str = "api") -> SendBatch:
        batch = self.get(batch_id)
        if not repo.claim_batch_executing(self._session, batch_id):
            raise ConflictError(f"The batch cannot be executed from status {batch.status.value}")
        repo.commit(self._session)

        for item in repo.list_items(self._session, batch_id):
            if item.status not in repo.RETRYABLE_ITEM_STATUSES:
                continue  # already terminal (sent/excluded/skipped): never re-attempted
            self._attempt(item, batch_id=batch_id, actor=actor)

        items = repo.list_items(self._session, batch_id)
        any_failed = any(i.status is SendBatchItemStatus.FAILED for i in items)
        final = SendBatchStatus.PARTIALLY_FAILED if any_failed else SendBatchStatus.COMPLETED
        batch.status = final
        event = (
            AuditEventType.SEND_BATCH_PARTIALLY_FAILED
            if any_failed
            else AuditEventType.SEND_BATCH_COMPLETED
        )
        sent = sum(1 for i in items if i.status is SendBatchItemStatus.SENT)
        failed = sum(1 for i in items if i.status is SendBatchItemStatus.FAILED)
        excluded_or_skipped = len(items) - sent - failed
        AuditLog(self._session).record(
            event,
            actor=actor,
            subject=f"batch:{batch_id}",
            details={
                "correlation_id": batch.correlation_id,
                "created": sent,
                "rejected": failed,
                "matched": excluded_or_skipped,
            },
        )
        self._session.commit()
        self._session.refresh(batch)
        return batch

    def _attempt(self, item: SendBatchItem, *, batch_id: int, actor: str) -> None:
        if not repo.claim_item_sending(self._session, item.id):
            return  # a concurrent attempt already claimed this item
        is_retry = item.attempts > 0
        if item.message_id is None:
            # Generated ONCE and reused verbatim on every later retry of this SAME item, so a
            # provider that supports `IdempotentMailProvider` can be asked "was this already
            # sent?" using the exact id a previous, possibly-lost-in-transit attempt would have
            # used - never regenerated, or the lookup would search for the wrong id.
            item.message_id = self._new_message_id(item)
        self._session.commit()

        package = self._session.get(ApplicationPackage, item.application_package_id)
        assert package is not None
        failure = self._preflight(package)
        if failure is not None:
            item.status = SendBatchItemStatus.EXCLUDED
            item.failure_reason = failure
            item.attempts += 1
            self._session.commit()
            return

        if is_retry:
            found = self._find_already_sent(item.message_id)
            if found is not None:
                item.status = SendBatchItemStatus.SENT
                item.provider = found.provider
                item.provider_message_id = found.provider_message_id
                item.thread_id = found.thread_id
                item.sent_at = datetime.now(UTC)
                item.attempts += 1
                self._session.commit()
                return

        email = self._build_email(package, message_id=item.message_id)
        AuditLog(self._session).record(
            AuditEventType.SEND_REQUESTED,
            actor=actor,
            subject=f"application:{package.id}",
            details={"batch_id": batch_id, "package_id": package.id},
        )
        self._session.commit()

        try:
            result = self._mail_sender.send(email, approved=True)
        except Exception:
            item.status = SendBatchItemStatus.FAILED
            item.failure_reason = "provider_error"
            item.attempts += 1
            self._session.commit()
            return

        if result.sent is None:  # SendGuard blocked it (e.g. SEND_MODE not auto/dry_run/manual)
            item.status = SendBatchItemStatus.FAILED
            item.failure_reason = result.decision.reason
            item.attempts += 1
            self._session.commit()
            return

        item.status = SendBatchItemStatus.SENT
        item.provider = result.sent.provider
        item.provider_message_id = result.sent.provider_message_id
        item.thread_id = result.sent.thread_id
        item.sent_at = datetime.now(UTC)
        item.attempts += 1
        self._session.commit()

    def _new_message_id(self, item: SendBatchItem) -> str:
        sender = self._settings.mail_from or "invalid.example"
        return make_msgid(
            idstring=f"batch-{item.batch_id}-item-{item.id}", domain=recipient_domain(sender)
        )

    def _find_already_sent(self, message_id: str) -> SentMessage | None:
        """Best-effort (see docs/send_batches.md): `None` on ANY doubt (unsupported provider,
        the lookup itself failing) - never treated as proof nothing was sent; a normal retry
        follows either way. Never raises: a lookup failure must not block the retry it guards."""
        provider = self._mail_sender.live_provider
        if not isinstance(provider, IdempotentMailProvider):
            return None
        try:
            return provider.find_existing(message_id)
        except Exception:
            return None

    def _preflight(self, package: ApplicationPackage) -> str | None:
        """Specific, actionable reasons are checked before the general `stale` catch-all: a
        contact flip (e.g. `do_not_contact` turning true) also changes that contact's
        `updated_at`, which `is_stale` would legitimately flag too - but "do_not_contact" tells
        a human far more than "stale" does, so it is reported first when both are true."""
        if not self._settings.mail_from:
            return "no_sender_configured"
        if package.status is not ApplicationPackageStatus.APPROVED:
            return "package_not_approved"
        if repo.has_sent_item_for_package(self._session, package.id):
            return "already_sent"
        if package.contact_id is None:
            return "no_accepted_contact"
        contact = self._session.get(Contact, package.contact_id)
        if contact is None or contact.do_not_contact:
            return "do_not_contact"
        if best_email_channel(contact) is None:
            return "no_email"
        if package.cv_document_id is None:
            return "no_cv_reference"
        if self._packages.is_stale(package):
            return "stale"
        return None

    def _build_email(self, package: ApplicationPackage, *, message_id: str) -> OutgoingEmail:
        assert package.contact_id is not None
        contact = self._session.get(Contact, package.contact_id)
        assert contact is not None
        channel = best_email_channel(contact)
        assert channel is not None
        assert package.draft_id is not None
        draft = self._session.get(ApplicationDraft, package.draft_id)
        assert draft is not None
        assert package.cv_document_id is not None
        cv = self._session.get(DocumentIngestion, package.cv_document_id)
        assert cv is not None
        return OutgoingEmail(
            sender=self._settings.mail_from or "",
            to=channel.value,
            subject=draft.subject,
            body_text=draft.body,
            attachments=(AttachmentRef(relative_path=cv.source_uri),),
            message_id=message_id,
        )
