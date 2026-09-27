"""SEND_MODE configuration and the SendGuard decision table."""

import pytest
from pydantic import ValidationError

from app.core.config import SendMode, Settings
from app.services.send_guard import SendAction, SendGuard

ME = "me.fixture@example.invalid"
OTHER = "someone.fixture@example.invalid"


def guard(monkeypatch: pytest.MonkeyPatch, mode: str | None, allowed: str = "") -> SendGuard:
    if mode is not None:
        monkeypatch.setenv("SEND_MODE", mode)
    monkeypatch.setenv("SEND_ALLOWED_RECIPIENTS", allowed)
    return SendGuard(Settings())


# --- Configuration ---------------------------------------------------------------------


def test_send_mode_defaults_to_disabled() -> None:
    assert Settings().send_mode is SendMode.DISABLED


def test_send_mode_auto_is_available_since_step_10(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_MODE", "auto")

    assert Settings().send_mode is SendMode.AUTO


@pytest.mark.parametrize("value", ["", "enabled", "send", "AUTO ", "true"])
def test_unknown_send_modes_are_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SEND_MODE", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["disabled", "dry_run", "manual", "auto"])
def test_supported_send_modes(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SEND_MODE", value)

    assert Settings().send_mode.value == value


def test_allowed_recipients_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SEND_ALLOWED_RECIPIENTS", " Me.Fixture@Example.invalid , ,b@example.invalid"
    )

    assert Settings().allowed_recipient_list == ["me.fixture@example.invalid", "b@example.invalid"]


# --- Decisions -------------------------------------------------------------------------


@pytest.mark.parametrize("approved", [False, True])
def test_disabled_blocks_everything_even_approved_and_allowed(
    monkeypatch: pytest.MonkeyPatch, approved: bool
) -> None:
    decision = guard(monkeypatch, None, allowed=ME).evaluate(ME, approved=approved)

    assert decision.action is SendAction.BLOCK and decision.reason == "send_disabled"


@pytest.mark.parametrize("approved", [False, True])
def test_dry_run_only_ever_writes_locally(monkeypatch: pytest.MonkeyPatch, approved: bool) -> None:
    decision = guard(monkeypatch, "dry_run").evaluate(OTHER, approved=approved)

    assert decision.action is SendAction.DRY_RUN  # and therefore never SEND


def test_manual_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "manual", allowed=ME).evaluate(ME, approved=False)

    assert decision.action is SendAction.BLOCK and decision.reason == "not_approved"


def test_manual_requires_an_allowed_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "manual", allowed=ME).evaluate(OTHER, approved=True)

    assert decision.action is SendAction.BLOCK and decision.reason == "recipient_not_allowed"


def test_manual_with_empty_allow_list_sends_to_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "manual").evaluate(ME, approved=True)

    assert decision.action is SendAction.BLOCK and decision.reason == "recipient_not_allowed"


def test_manual_sends_only_when_approved_and_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "manual", allowed=ME).evaluate(ME.upper(), approved=True)

    assert decision.action is SendAction.SEND and decision.reason == "approved_manual"
    assert decision.recipient_domain == "example.invalid"


def test_no_domain_wildcards_in_the_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "manual", allowed="@example.invalid").evaluate(ME, approved=True)

    assert decision.action is SendAction.BLOCK


@pytest.mark.parametrize(
    "recipient",
    [
        "",
        "not-an-address",
        f"{ME},{OTHER}",
        f"{ME};{OTHER}",
        f"Name <{ME}>",
        f"{ME}\r\nBcc: {OTHER}",
        f"{ME}\nBcc: {OTHER}",
        f" {OTHER} extra",
    ],
)
def test_invalid_or_injected_recipients_are_blocked(
    monkeypatch: pytest.MonkeyPatch, recipient: str
) -> None:
    decision = guard(monkeypatch, "dry_run").evaluate(recipient, approved=True)

    assert decision.action is SendAction.BLOCK and decision.reason == "invalid_recipient"


def test_an_unrecognised_mode_blocks_defensively(monkeypatch: pytest.MonkeyPatch) -> None:
    """`auto` is now a real, handled mode (step 10); the defensive fallback (for a future enum
    value this guard does not yet know about) is exercised by forcing an impossible internal
    value directly, since every real `SendMode` is now covered."""
    instance = guard(monkeypatch, "disabled")
    instance._mode = "bogus_future_mode"  # type: ignore[assignment]

    decision = instance.evaluate(ME, approved=True)

    assert decision.action is SendAction.BLOCK and decision.reason == "mode_unavailable"


# --- auto (step 10): explicit approval, no recipient allow-list -----------------------------


def test_auto_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "auto").evaluate(OTHER, approved=False)

    assert decision.action is SendAction.BLOCK and decision.reason == "not_approved"


def test_auto_sends_when_approved_with_no_allow_list_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately different from `manual`: a real professional contact's address cannot be
    pre-enumerated the way a bootstrapping test address can - the explicit approval IS the
    control (see app.services.send_batch's two-layer approval)."""
    decision = guard(monkeypatch, "auto").evaluate(OTHER, approved=True)  # not in any allow-list

    assert decision.action is SendAction.SEND and decision.reason == "approved_auto"
    assert decision.recipient_domain == "example.invalid"


def test_auto_still_blocks_an_invalid_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = guard(monkeypatch, "auto").evaluate("not-an-address", approved=True)

    assert decision.action is SendAction.BLOCK and decision.reason == "invalid_recipient"
