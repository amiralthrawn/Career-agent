"""Sends (or refuses to send) a message: guard -> audit -> provider, failing closed.

Order matters:
1. the `SendGuard` decides;
2. the decision is recorded in the audit trail BEFORE anything happens; if the audit cannot be
   written, nothing is sent (no audit, no send);
3. only then is the provider called, and its outcome is audited.

The audit only receives short codes, the recipient's domain and counters: never the subject,
the body, an attachment name or an address.
"""

from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.mail.dry_run import DryRunMailProvider
from app.integrations.mail.mime import build_message
from app.integrations.mail.ports import MailProvider, OutgoingEmail, SentMessage
from app.models.audit import AuditEventType
from app.services.audit import AuditLog
from app.services.send_guard import SendAction, SendDecision, SendGuard


@dataclass(frozen=True)
class SendResult:
    decision: SendDecision
    sent: SentMessage | None  # None when blocked


class MailSender:
    def __init__(
        self,
        settings: Settings,
        audit: AuditLog,
        *,
        live_provider: MailProvider | None = None,
        dry_run_provider: MailProvider | None = None,
        actor: str = "system",
    ) -> None:
        self._settings = settings
        self._audit = audit
        self._guard = SendGuard(settings)
        self._live = live_provider  # no live provider exists yet (Gmail comes later)
        self._dry_run = dry_run_provider or DryRunMailProvider(
            settings.private_data_path, settings.outbox_path
        )
        self._actor = actor

    @property
    def live_provider(self) -> MailProvider | None:
        """The configured real provider, if any (step 10: so a caller can check whether it also
        satisfies `IdempotentMailProvider`, e.g. `app.services.send_batch`) - never used to
        bypass `SendGuard`; still only ever called through `send()`."""
        return self._live

    def send(self, email: OutgoingEmail, *, approved: bool = False) -> SendResult:
        decision = self._guard.evaluate(email.to, approved=approved)
        details = self._details(decision)

        if decision.action is SendAction.BLOCK:
            self._audit.record(AuditEventType.SEND_BLOCKED, actor=self._actor, details=details)
            return SendResult(decision, None)

        try:
            built = build_message(self._settings, email)  # validates and reads attachments
        except (UnprocessableError, NotFoundError):
            invalid = {**details, "reason": "invalid_message"}
            self._audit.record(AuditEventType.SEND_BLOCKED, actor=self._actor, details=invalid)
            raise
        details = {
            **details,
            "attachments": built.attachment_count,
            "bytes": built.attachment_bytes,
        }

        if decision.action is SendAction.DRY_RUN:
            self._audit.record(AuditEventType.SEND_DRY_RUN, actor=self._actor, details=details)
            return SendResult(decision, self._dry_run.send(built))

        # A real send: refuse if no live provider is configured, and audit first.
        if self._live is None:
            blocked = {**details, "reason": "no_live_provider"}
            self._audit.record(AuditEventType.SEND_BLOCKED, actor=self._actor, details=blocked)
            raise ConflictError("No live mail provider is configured")
        self._audit.record(AuditEventType.SEND_APPROVED, actor=self._actor, details=details)
        try:
            sent = self._live.send(built)
        except Exception:
            self._audit.record(AuditEventType.SEND_FAILED, actor=self._actor, details=details)
            raise
        self._audit.record(AuditEventType.SEND_SENT, actor=self._actor, details=details)
        return SendResult(decision, sent)

    @staticmethod
    def _details(decision: SendDecision) -> dict[str, str | int | bool | None]:
        return {
            "reason": decision.reason,
            "mode": decision.mode.value,
            "recipient_domain": decision.recipient_domain,
        }
