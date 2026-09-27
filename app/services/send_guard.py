"""The single decision point for outgoing e-mail.

Nothing may reach a provider without going through `SendGuard.evaluate`. It is a pure
function of the configuration and of the request, so its behaviour is easy to test.

- `disabled`: everything is blocked.
- `dry_run`: everything is written as a local .eml, never sent.
- `manual`: a real send needs an explicitly approved draft AND a recipient from the allow-list
  (bootstrapping mode: your own address only).
- `auto` (step 10): a real send needs `approved=True` - which `app.services.send_batch` only
  ever passes after an `ApplicationPackage` was individually approved (step 9, unchanged) AND its
  `SendBatch` was explicitly approved (step 10) AND every send-time precondition still holds. No
  recipient allow-list here: the explicit two-layer approval IS the control, not a fixed address
  list (a real professional contact's address cannot be pre-enumerated the way a bootstrapping
  test address can).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.config import SendMode, Settings
from app.core.errors import UnprocessableError
from app.integrations.mail.ports import recipient_domain, validate_address


class SendAction(StrEnum):
    BLOCK = "block"
    DRY_RUN = "dry_run"
    SEND = "send"


@dataclass(frozen=True)
class SendDecision:
    action: SendAction
    reason: str  # short machine-readable code (safe to audit)
    mode: SendMode
    recipient_domain: str | None = None


class SendGuard:
    def __init__(self, settings: Settings) -> None:
        self._mode = settings.send_mode
        self._allowed = frozenset(settings.allowed_recipient_list)

    def evaluate(self, recipient: str, *, approved: bool = False) -> SendDecision:
        try:
            address = validate_address(recipient)
            domain: str | None = recipient_domain(address)
        except UnprocessableError:
            return SendDecision(SendAction.BLOCK, "invalid_recipient", self._mode)

        if self._mode is SendMode.DISABLED:
            return SendDecision(SendAction.BLOCK, "send_disabled", self._mode, domain)
        if self._mode is SendMode.DRY_RUN:
            return SendDecision(SendAction.DRY_RUN, "dry_run", self._mode, domain)
        if self._mode is SendMode.MANUAL:
            if not approved:
                return SendDecision(SendAction.BLOCK, "not_approved", self._mode, domain)
            if address.lower() not in self._allowed:
                return SendDecision(SendAction.BLOCK, "recipient_not_allowed", self._mode, domain)
            return SendDecision(SendAction.SEND, "approved_manual", self._mode, domain)
        if self._mode is SendMode.AUTO:
            if not approved:
                return SendDecision(SendAction.BLOCK, "not_approved", self._mode, domain)
            return SendDecision(SendAction.SEND, "approved_auto", self._mode, domain)
        return SendDecision(SendAction.BLOCK, "mode_unavailable", self._mode, domain)
