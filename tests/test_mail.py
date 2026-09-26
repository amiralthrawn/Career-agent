"""MIME builder, dry-run provider and MailSender (guard -> audit -> provider), all offline."""

import socket
import subprocess
from collections.abc import Callable
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.mail.dry_run import DryRunMailProvider
from app.integrations.mail.mime import MAX_ATTACHMENT_BYTES, build_message
from app.integrations.mail.ports import (
    MAX_ATTACHMENTS,
    AttachmentRef,
    BuiltMessage,
    OutgoingEmail,
    SentMessage,
)
from app.models import AuditEvent
from app.models.audit import AuditEventType
from app.services.audit import AuditLog
from app.services.mail_sender import MailSender
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx

SENDER = "sender.fixture@example.invalid"
RECIPIENT = "recipient.fixture@example.invalid"
BINARY_PAYLOAD = bytes(range(256)) * 40  # every byte value, to prove byte-for-byte fidelity
BODY = "Bonjour,\n\nCandidature synthétique — accents éàü et emoji-free.\n"
SUBJECT = "Candidature synthétique : test"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to open a network connection fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access is forbidden in these tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def settings(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SEND_MODE", "dry_run")
    monkeypatch.setenv("MAIL_FROM", SENDER)
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def cv(private_dir: Path) -> Path:
    path = private_dir / "documents" / "cv.docx"
    path.write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    return path


def email(*attachments: str, to: str = RECIPIENT) -> OutgoingEmail:
    return OutgoingEmail(
        sender=SENDER,
        to=to,
        subject=SUBJECT,
        body_text=BODY,
        attachments=tuple(AttachmentRef(path) for path in attachments),
    )


def parse(raw: bytes) -> EmailMessage:
    message = message_from_bytes(raw, policy=policy.default)
    assert isinstance(message, EmailMessage)
    return message


# --- MIME builder ----------------------------------------------------------------------


def test_message_has_the_expected_headers_and_utf8_body(settings: Settings) -> None:
    built = build_message(settings, email())

    message = parse(built.raw)
    assert (message["From"], message["To"], message["Subject"]) == (SENDER, RECIPIENT, SUBJECT)
    assert message["Message-ID"] == built.message_id and message["Date"]
    body = message.get_body(preferencelist=("plain",))
    assert body is not None and body.get_content().replace("\r\n", "\n") == BODY
    assert built.attachment_count == 0 and built.attachment_bytes == 0


def test_attachment_is_identical_to_the_original_byte_for_byte(
    settings: Settings, cv: Path
) -> None:
    original = cv.read_bytes()

    built = build_message(settings, email("documents/cv.docx"))

    (attachment,) = list(parse(built.raw).iter_attachments())
    assert attachment.get_content() == original  # exact bytes
    assert attachment.get_filename() == "cv.docx"
    assert attachment.get_content_type().endswith("wordprocessingml.document")
    assert built.attachment_count == 1 and built.attachment_bytes == len(original)
    import hashlib

    assert built.attachment_sha256 == (hashlib.sha256(original).hexdigest(),)
    assert cv.read_bytes() == original  # the source file is untouched


def test_every_byte_value_survives_the_encoding(settings: Settings, private_dir: Path) -> None:
    (private_dir / "documents" / "blob.pdf").write_bytes(BINARY_PAYLOAD)

    built = build_message(settings, email("documents/blob.pdf"))

    (attachment,) = list(parse(built.raw).iter_attachments())
    assert attachment.get_content() == BINARY_PAYLOAD
    assert attachment.get_content_type() == "application/pdf"


def test_attachment_display_name_can_differ_from_the_file_name(
    settings: Settings, cv: Path
) -> None:
    message = OutgoingEmail(
        sender=SENDER,
        to=RECIPIENT,
        subject=SUBJECT,
        body_text=BODY,
        attachments=(AttachmentRef("documents/cv.docx", filename="CV-Fixture.docx"),),
    )

    (attachment,) = list(parse(build_message(settings, message).raw).iter_attachments())

    assert attachment.get_filename() == "CV-Fixture.docx"


@pytest.mark.parametrize("path", ["../outside.docx", "/abs/cv.docx", "C:\\Users\\x\\cv.docx"])
def test_attachment_outside_the_private_directory_is_refused(
    settings: Settings, cv: Path, path: str
) -> None:
    with pytest.raises(UnprocessableError, match="private data directory"):
        build_message(settings, email(path))


def test_attachment_escaping_through_a_link_is_refused(
    settings: Settings, private_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cv.docx").write_bytes(b"secret outside the private directory")
    link = private_dir / "documents" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # no symlink privilege on Windows: a junction needs none
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if result.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available")

    with pytest.raises(UnprocessableError, match="private data directory"):
        build_message(settings, email("documents/escape/cv.docx"))


def test_unsupported_and_missing_attachments_are_refused(
    settings: Settings, private_dir: Path
) -> None:
    (private_dir / "documents" / "tool.exe").write_bytes(b"MZ")

    with pytest.raises(UnprocessableError, match="Unsupported document type"):
        build_message(settings, email("documents/tool.exe"))
    with pytest.raises(NotFoundError):
        build_message(settings, email("documents/absent.docx"))


def test_oversized_attachment_is_refused(settings: Settings, private_dir: Path) -> None:
    (private_dir / "documents" / "big.pdf").write_bytes(b"0" * (MAX_ATTACHMENT_BYTES + 1))
    (private_dir / "documents" / "limit.pdf").write_bytes(b"0" * MAX_ATTACHMENT_BYTES)

    with pytest.raises(UnprocessableError, match="too large"):
        build_message(settings, email("documents/big.pdf"))
    assert build_message(settings, email("documents/limit.pdf")).attachment_count == 1


def test_too_many_attachments_are_refused() -> None:
    with pytest.raises(UnprocessableError):
        email(*["documents/cv.docx"] * (MAX_ATTACHMENTS + 1))


@pytest.mark.parametrize("name", ["", "a/b.docx", "a\\b.docx", 'a".docx', "a\r\nBcc: x.docx"])
def test_unsafe_attachment_display_names_are_refused(
    settings: Settings, cv: Path, name: str
) -> None:
    message = OutgoingEmail(
        sender=SENDER,
        to=RECIPIENT,
        subject=SUBJECT,
        body_text=BODY,
        attachments=(AttachmentRef("documents/cv.docx", filename=name),),
    )

    with pytest.raises(UnprocessableError):
        build_message(settings, message)


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject": ""},
        {"subject": "Hello\r\nBcc: evil@example.invalid"},
        {"subject": "x" * 201},
        {"body_text": "   "},
        {"body_text": "x" * 50_001},
        {"to": f"{RECIPIENT}, other@example.invalid"},
        {"sender": "Name <a@example.invalid>"},
    ],
)
def test_invalid_messages_are_refused(overrides: dict[str, str]) -> None:
    fields: dict[str, Any] = {
        "sender": SENDER,
        "to": RECIPIENT,
        "subject": SUBJECT,
        "body_text": BODY,
    }

    with pytest.raises(UnprocessableError):
        OutgoingEmail(**{**fields, **overrides})


# --- Dry-run provider ------------------------------------------------------------------


def test_dry_run_writes_one_eml_inside_the_outbox_only(
    settings: Settings, cv: Path, private_dir: Path, no_network: None
) -> None:
    built = build_message(settings, email("documents/cv.docx"))
    before = {p for p in private_dir.rglob("*") if p.is_file()}

    sent = DryRunMailProvider(settings.private_data_path, settings.outbox_path).send(built)

    created = {p for p in private_dir.rglob("*") if p.is_file()} - before
    (path,) = created
    assert path.parent == private_dir / "outbox" and path.suffix == ".eml"
    assert path.read_bytes() == built.raw
    assert sent.provider == "dry_run" and sent.location == f"outbox/{path.name}"
    assert sent.provider_message_id == built.message_id
    assert RECIPIENT not in path.name and "fixture" not in path.name  # no PII in the name
    assert not list((private_dir / "outbox").glob("*.part"))


def test_dry_run_never_overwrites_an_existing_file(settings: Settings) -> None:
    provider = DryRunMailProvider(settings.private_data_path, settings.outbox_path)
    built = build_message(settings, email())
    provider.send(built)

    with pytest.raises(ConflictError):
        provider.send(built)  # same message id and second: same file name


def test_outbox_must_be_the_private_outbox(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(UnprocessableError):
        DryRunMailProvider(settings.private_data_path, tmp_path / "elsewhere" / "outbox")
    with pytest.raises(UnprocessableError):
        DryRunMailProvider(settings.private_data_path, settings.private_data_path / "documents")
    with pytest.raises(UnprocessableError):
        DryRunMailProvider(settings.private_data_path, settings.private_data_path)


# --- MailSender: guard -> audit -> provider --------------------------------------------


class RecordingProvider:
    """Fake live provider: records what it receives; never touches a network."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[BuiltMessage] = []
        self.error = error

    def send(self, message: BuiltMessage) -> SentMessage:
        self.calls.append(message)
        if self.error:
            raise self.error
        return SentMessage(provider="fake", provider_message_id="fake-id-1", thread_id="thread-1")


def events(open_session: Callable[[], Session]) -> list[AuditEvent]:
    with open_session() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))


def make_sender(
    settings: Settings, open_session: Callable[[], Session], **kwargs: Any
) -> tuple[MailSender, Session]:
    session = open_session()
    return MailSender(settings, AuditLog(session), **kwargs), session


def use_mode(monkeypatch: pytest.MonkeyPatch, mode: str, allowed: str = "") -> Settings:
    monkeypatch.setenv("SEND_MODE", mode)
    monkeypatch.setenv("SEND_ALLOWED_RECIPIENTS", allowed)
    get_settings.cache_clear()
    return get_settings()


def outbox_files(private_dir: Path) -> list[Path]:
    outbox = private_dir / "outbox"
    return list(outbox.glob("*")) if outbox.exists() else []


def test_disabled_mode_sends_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    provider = RecordingProvider()
    sender, session = make_sender(
        use_mode(monkeypatch, "disabled", RECIPIENT), configured_database, live_provider=provider
    )

    result = sender.send(email("documents/cv.docx"), approved=True)

    assert result.sent is None and result.decision.reason == "send_disabled"
    assert provider.calls == [] and outbox_files(private_dir) == []
    (recorded,) = events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_BLOCKED
    assert recorded.details == {
        "reason": "send_disabled",
        "mode": "disabled",
        "recipient_domain": "example.invalid",
    }
    session.close()


def test_dry_run_writes_an_eml_with_the_attachment_and_audits_it(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    provider = RecordingProvider()
    sender, session = make_sender(
        use_mode(monkeypatch, "dry_run"), configured_database, live_provider=provider
    )

    result = sender.send(email("documents/cv.docx"))

    assert result.sent is not None and result.sent.provider == "dry_run"
    (path,) = outbox_files(private_dir)
    (attachment,) = list(parse(path.read_bytes()).iter_attachments())
    assert attachment.get_content() == cv.read_bytes()
    assert provider.calls == []  # a dry run never reaches the live provider
    (recorded,) = events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_DRY_RUN
    assert recorded.details["attachments"] == 1 and recorded.details["bytes"] == len(
        cv.read_bytes()
    )
    session.close()


def test_manual_mode_sends_only_approved_mail_to_allowed_recipients(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    cv: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    provider = RecordingProvider()
    settings = use_mode(monkeypatch, "manual", RECIPIENT)
    sender, session = make_sender(settings, configured_database, live_provider=provider)

    unapproved = sender.send(email("documents/cv.docx"), approved=False)
    elsewhere = sender.send(email(to="other.fixture@example.invalid"), approved=True)
    assert unapproved.sent is None and elsewhere.sent is None and provider.calls == []

    sent = sender.send(email("documents/cv.docx"), approved=True)

    assert sent.sent is not None and sent.sent.provider_message_id == "fake-id-1"
    (call,) = provider.calls
    assert call.to == RECIPIENT and call.attachment_count == 1
    kinds = [e.event_type for e in events(configured_database)]
    assert kinds == [
        AuditEventType.SEND_BLOCKED,
        AuditEventType.SEND_BLOCKED,
        AuditEventType.SEND_APPROVED,  # audited BEFORE the provider is called
        AuditEventType.SEND_SENT,
    ]
    assert outbox_files(private_dir) == []
    session.close()


def test_manual_mode_without_a_live_provider_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    sender, session = make_sender(use_mode(monkeypatch, "manual", RECIPIENT), configured_database)

    with pytest.raises(ConflictError, match="No live mail provider"):
        sender.send(email(), approved=True)

    (recorded,) = events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_BLOCKED
    assert recorded.details["reason"] == "no_live_provider"
    session.close()


def test_provider_failure_is_audited_and_propagated(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    provider = RecordingProvider(error=RuntimeError("provider down"))
    settings = use_mode(monkeypatch, "manual", RECIPIENT)
    sender, session = make_sender(settings, configured_database, live_provider=provider)

    with pytest.raises(RuntimeError, match="provider down"):
        sender.send(email(), approved=True)

    kinds = [e.event_type for e in events(configured_database)]
    assert kinds == [AuditEventType.SEND_APPROVED, AuditEventType.SEND_FAILED]
    session.close()


class BrokenAudit(AuditLog):
    def record(self, *args: Any, **kwargs: Any) -> AuditEvent:
        raise RuntimeError("audit unavailable")


@pytest.mark.parametrize("mode", ["dry_run", "manual"])
def test_no_audit_means_no_send(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    mode: str,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    provider = RecordingProvider()
    settings = use_mode(monkeypatch, mode, RECIPIENT)
    with configured_database() as session:
        sender = MailSender(settings, BrokenAudit(session), live_provider=provider)

        with pytest.raises(RuntimeError, match="audit unavailable"):
            sender.send(email(), approved=True)

    assert provider.calls == [] and outbox_files(private_dir) == []


def test_invalid_attachment_is_audited_then_refused(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    sender, session = make_sender(use_mode(monkeypatch, "dry_run"), configured_database)

    with pytest.raises(UnprocessableError):
        sender.send(email("../outside.docx"))

    (recorded,) = events(configured_database)
    assert recorded.event_type is AuditEventType.SEND_BLOCKED
    assert recorded.details["reason"] == "invalid_message"
    assert outbox_files(private_dir) == []
    session.close()


def test_audit_holds_no_mail_content_secret_or_address(
    monkeypatch: pytest.MonkeyPatch,
    private_dir: Path,
    configured_database: Callable[[], Session],
    no_network: None,
) -> None:
    secret_body = "SYNTHETIC-BODY-MARKER"
    secret_subject = "SYNTHETIC-SUBJECT-MARKER"
    (private_dir / "documents" / "SYNTHETIC-FILENAME-MARKER.docx").write_bytes(b"attachment")
    message = OutgoingEmail(
        sender=SENDER,
        to=RECIPIENT,
        subject=secret_subject,
        body_text=secret_body,
        attachments=(AttachmentRef("documents/SYNTHETIC-FILENAME-MARKER.docx"),),
    )
    for mode, approved in (("disabled", True), ("dry_run", False), ("manual", False)):
        sender, session = make_sender(use_mode(monkeypatch, mode, RECIPIENT), configured_database)
        sender.send(message, approved=approved)
        session.close()

    dump = repr(
        [(e.event_type, e.actor, e.subject, e.details) for e in events(configured_database)]
    )
    for forbidden in (
        secret_body,
        secret_subject,
        "SYNTHETIC-FILENAME-MARKER",
        RECIPIENT,
        SENDER,
        "recipient.fixture",
        "attachment",
    ):
        if forbidden == "attachment":  # the counter key "attachments" is allowed, never a name
            assert "attachment'" not in dump.replace("'attachments'", "")
        else:
            assert forbidden not in dump
    assert "example.invalid" in dump  # the recipient's domain is what is kept
