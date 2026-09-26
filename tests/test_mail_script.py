"""scripts/dry_run_mail.py: builds a local .eml, prints only counters, never sends."""

from collections.abc import Callable
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditEvent
from app.models.audit import AuditEventType
from scripts import dry_run_mail
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx
from tests.test_mail import RECIPIENT, SENDER, no_network  # noqa: F401  (fixture re-export)


@pytest.fixture
def cv(private_dir: Path) -> Path:
    path = private_dir / "documents" / "cv.docx"
    path.write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    return path


def configure(monkeypatch: pytest.MonkeyPatch, mode: str, sender: str | None = SENDER) -> None:
    monkeypatch.setenv("SEND_MODE", mode)
    if sender:
        monkeypatch.setenv("MAIL_FROM", sender)
    get_settings.cache_clear()


def audit_events(open_session: Callable[[], Session]) -> list[AuditEvent]:
    with open_session() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))


def test_dry_run_script_writes_an_eml_with_the_original_cv(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    capsys: pytest.CaptureFixture[str],
    no_network: None,  # noqa: F811
) -> None:
    configure(monkeypatch, "dry_run")

    code = dry_run_mail.main(["--to", RECIPIENT, "--attach", "documents/cv.docx"])

    assert code == 0
    (eml,) = list((private_dir / "outbox").glob("*.eml"))
    message = message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert isinstance(message, EmailMessage)
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_content() == cv.read_bytes()  # the CV, byte for byte
    output = capsys.readouterr().out
    assert f"outbox/{eml.name}" in output and "no network call was made" in output
    for forbidden in (RECIPIENT, SENDER, "cv.docx", "Synthetic test message"):
        assert forbidden not in output
    (recorded,) = audit_events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_DRY_RUN


def test_script_refuses_when_sending_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    capsys: pytest.CaptureFixture[str],
    no_network: None,  # noqa: F811
) -> None:
    configure(monkeypatch, "disabled")

    code = dry_run_mail.main(["--to", RECIPIENT, "--attach", "documents/cv.docx"])

    assert code == 1
    assert "block (send_disabled)" in capsys.readouterr().out
    assert not (private_dir / "outbox").exists()
    (recorded,) = audit_events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_BLOCKED


def test_script_never_approves_a_real_send(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    configured_database: Callable[[], Session],
    capsys: pytest.CaptureFixture[str],
    no_network: None,  # noqa: F811
) -> None:
    monkeypatch.setenv("SEND_ALLOWED_RECIPIENTS", RECIPIENT)
    configure(monkeypatch, "manual")

    code = dry_run_mail.main(["--to", RECIPIENT])

    assert code == 1 and "block (not_approved)" in capsys.readouterr().out
    assert not (private_dir / "outbox").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--to", RECIPIENT, "--attach", "../outside.docx"],
        ["--to", RECIPIENT, "--attach", "documents/absent.docx"],
        ["--to", f"{RECIPIENT}, other@example.invalid"],
    ],
)
def test_script_reports_invalid_input_generically(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    configure(monkeypatch, "dry_run")

    assert dry_run_mail.main(arguments) == 1

    captured = capsys.readouterr()
    assert captured.err.startswith("Error: ") and captured.out == ""
    assert not (private_dir / "outbox").exists()


def test_script_needs_a_sender_and_a_database(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    capsys: pytest.CaptureFixture[str],
    configured_database: Callable[[], Session],
) -> None:
    configure(monkeypatch, "dry_run", sender=None)
    assert dry_run_mail.main(["--to", RECIPIENT]) == 1
    assert "MAIL_FROM" in capsys.readouterr().err

    monkeypatch.delenv("DATABASE_URL")
    configure(monkeypatch, "dry_run")
    assert dry_run_mail.main(["--to", RECIPIENT]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err
    assert not (private_dir / "outbox").exists()
