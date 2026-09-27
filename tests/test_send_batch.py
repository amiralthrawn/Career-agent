"""Controlled batch sending (step 10): individual vs batch validation, atomic-ish batch send,
partial failure, idempotence, security preconditions, audit. All synthetic, no network, no real
Gmail call - a fake `MailProvider` stands in for `GmailClient`.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, UnprocessableError
from app.integrations.mail.ports import BuiltMessage, MailProvider, SentMessage
from app.models import (
    ApplicationPackage,
    AuditEvent,
    Contact,
    ContactChannel,
    DocumentIngestion,
    SendBatch,
    Target,
    TargetContact,
)
from app.models.audit import AuditEventType
from app.models.enums import (
    ChannelKind,
    InfoStatus,
    SendBatchItemStatus,
    SendBatchStatus,
)
from app.schemas.application_package import ApplicationPrepareRequest
from app.services.application_package import ApplicationPackageService
from app.services.audit import AuditLog
from app.services.mail_sender import MailSender
from app.services.send_batch import SendBatchService
from tests import targets_factory as f
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import add_target, crit, make_profile, qualify
from tests.requirements_factory import extract

SENDER = "sender.fixture@example.invalid"


class FakeMailProvider:
    """A fake `MailProvider`: scripted outcomes (a `SentMessage`, or an exception to raise) or,
    with none given, a fresh success every call. Records every message it was asked to send."""

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self._outcomes = outcomes
        self.sent: list[BuiltMessage] = []

    def send(self, message: BuiltMessage) -> SentMessage:
        self.sent.append(message)
        if self._outcomes:  # once exhausted, fall back to a default success (a real retry)
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            assert isinstance(outcome, SentMessage)
            return outcome
        return SentMessage(
            provider="fake", provider_message_id=f"fake-{len(self.sent)}", thread_id=None
        )


class FakeGmailLikeProvider:
    """A fake provider that ALSO satisfies `IdempotentMailProvider` (unlike `FakeMailProvider`,
    which deliberately does not - so existing retry tests exercise the "unsupported" path)."""

    def __init__(
        self, outcomes: list[Any] | None = None, existing: dict[str, SentMessage] | None = None
    ) -> None:
        self._outcomes = outcomes
        self.existing = existing or {}
        self.sent: list[BuiltMessage] = []
        self.find_calls: list[str] = []

    def send(self, message: BuiltMessage) -> SentMessage:
        self.sent.append(message)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            assert isinstance(outcome, SentMessage)
            return outcome
        return SentMessage(
            provider="fake-gmail", provider_message_id=f"fake-{len(self.sent)}", thread_id=None
        )

    def find_existing(self, message_id: str) -> SentMessage | None:
        self.find_calls.append(message_id)
        return self.existing.get(message_id)


@pytest.fixture
def settings(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SEND_MODE", "auto")
    monkeypatch.setenv("MAIL_FROM", SENDER)
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def cv(private_dir: Path) -> Path:
    path = private_dir / "documents" / "cv.docx"
    path.write_bytes(simple_docx(SYNTHETIC_CV_LINES))
    return path


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def candidate_id_of(session: Session) -> int:
    from app.models import Candidate

    result = session.scalars(select(Candidate.id)).first()
    assert result is not None
    return result


def ingest_cv(session: Session, candidate_id: int, sha256: str = "a" * 64) -> DocumentIngestion:
    doc = DocumentIngestion(
        candidate_id=candidate_id,
        source_uri="documents/cv.docx",
        sha256=sha256,
        file_size=100,
        parser_version="v1",
        stats={},
    )
    session.add(doc)
    session.flush()
    return doc


def accept_contact(
    session: Session, target: Target, *, email: str = "jamie@fixture-corp.example.invalid"
) -> Contact:
    contact = f.contact(session, target.company_id, name="Jamie Fixture")
    session.add(
        ContactChannel(
            contact_id=contact.id,
            kind=ChannelKind.EMAIL,
            value=email,
            status=InfoStatus.FOUND,
            source_id=f.source(session).id,
        )
    )
    session.add(
        TargetContact(
            target_id=target.id,
            contact_id=contact.id,
            company_id=target.company_id,
            is_primary=True,
        )
    )
    session.flush()
    return contact


def make_target(client: TestClient, *, domain: str, name: str) -> dict[str, Any]:
    make_profile(
        client,
        [crit("contract_type", ["apprenticeship"], "required")],
        name=f"Fixture profile {domain}",
    )
    target = add_target(client, domain=domain, name=name)
    extract(client, target["id"])  # real TargetRequirements, so a Brain change can matter
    assert qualify(client, target["id"]).status_code in (200, 201)
    return target


def make_approved_package(
    client: TestClient,
    db_session: Session,
    *,
    domain: str,
    name: str,
    with_contact: bool = True,
    with_email: bool = True,
) -> ApplicationPackage:
    target_json = make_target(client, domain=domain, name=name)
    target = db_session.get(Target, target_json["id"])
    assert target is not None
    if with_contact:
        if with_email:
            accept_contact(db_session, target, email=f"jamie@{domain}")
        else:
            _accept_contact_no_email(db_session, target)

    packages = ApplicationPackageService(db_session, FakeLLMClient(), None, None)
    package = packages.prepare(target.id, ApplicationPrepareRequest())
    return packages.decide(package.id, approve=True)


def _accept_contact_no_email(session: Session, target: Target) -> Contact:
    contact = f.contact(session, target.company_id, name="No Email Person")
    session.add(
        TargetContact(
            target_id=target.id,
            contact_id=contact.id,
            company_id=target.company_id,
            is_primary=True,
        )
    )
    session.flush()
    return contact


def service(
    session: Session, settings: Settings, provider: MailProvider | None
) -> SendBatchService:
    packages = ApplicationPackageService(session, None, None, None)
    mail_sender = MailSender(settings, AuditLog(session), live_provider=provider, actor="api")
    return SendBatchService(session, settings, mail_sender, packages)


# --- individual vs batch validation (Part 2 / how the two layers combine) ---------------------


def test_a_package_not_individually_approved_cannot_be_added_to_a_batch(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    target_json = make_target(client, domain="a.invalid", name="A")
    target = db_session.get(Target, target_json["id"])
    assert target is not None
    accept_contact(db_session, target)
    packages = ApplicationPackageService(db_session, FakeLLMClient(), None, None)
    pending = packages.prepare(target.id, ApplicationPrepareRequest())  # never approved

    with pytest.raises(UnprocessableError):
        service(db_session, settings, FakeMailProvider()).create([pending.id])


def test_an_individual_send_is_a_batch_of_exactly_one(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())

    batch = svc.create([package.id])
    svc.approve(batch.id)
    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.SENT
    assert executed.status is SendBatchStatus.COMPLETED


def test_selecting_many_and_sending_the_batch_in_one_action(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    packages = [
        make_approved_package(client, db_session, domain=f"{i}.invalid", name=f"Company {i}")
        for i in range(3)
    ]
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)

    batch = svc.create([p.id for p in packages])
    svc.approve(batch.id)
    executed = svc.execute(batch.id)

    items = svc.items(executed.id)
    assert len(items) == 3
    assert all(item.status is SendBatchItemStatus.SENT for item in items)
    assert executed.status is SendBatchStatus.COMPLETED
    assert len(provider.sent) == 3


# --- batch creation --------------------------------------------------------------------------


def test_create_refuses_an_empty_list(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    with pytest.raises(UnprocessableError):
        service(db_session, settings, FakeMailProvider()).create([])


def test_create_refuses_duplicate_ids(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")

    with pytest.raises(UnprocessableError):
        service(db_session, settings, FakeMailProvider()).create([package.id, package.id])


def test_create_is_idempotent_via_idempotency_key(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())

    first = svc.create([package.id], idempotency_key="key-1")
    second = svc.create([package.id], idempotency_key="key-1")

    assert first.id == second.id
    assert db_session.scalar(select(SendBatch).where(SendBatch.id == first.id)) is not None
    count = len(db_session.scalars(select(SendBatch)).all())
    assert count == 1


# --- validation: stale / do_not_contact / no email / already sent (Part 4) --------------------


def test_a_stale_package_is_excluded_at_execution_never_sent(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    # A change to the Candidate Brain after approval makes the underlying qualification stale.
    client.post("/api/candidate/skills", json={"name": "Docker"})
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.EXCLUDED
    assert item.failure_reason == "stale"
    assert provider.sent == []


def test_do_not_contact_excludes_the_item(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    target_json = make_target(client, domain="a.invalid", name="A")
    target = db_session.get(Target, target_json["id"])
    assert target is not None
    contact = accept_contact(db_session, target)
    packages = ApplicationPackageService(db_session, FakeLLMClient(), None, None)
    package = packages.decide(
        packages.prepare(target.id, ApplicationPrepareRequest()).id, approve=True
    )
    contact.do_not_contact = True
    db_session.commit()
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.EXCLUDED
    assert item.failure_reason == "do_not_contact"
    assert provider.sent == []


def test_no_email_channel_excludes_the_item(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(
        client, db_session, domain="a.invalid", name="A", with_email=False
    )
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.EXCLUDED
    assert item.failure_reason == "no_email"


def test_no_accepted_contact_excludes_the_item(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(
        client, db_session, domain="a.invalid", name="A", with_contact=False
    )
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.EXCLUDED
    assert item.failure_reason == "no_accepted_contact"


def test_no_cv_reference_excludes_the_item(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    # deliberately never call ingest_cv
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.EXCLUDED
    assert item.failure_reason == "no_cv_reference"


def test_a_package_already_sent_cannot_be_added_to_a_new_batch(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())
    first = svc.create([package.id])
    svc.approve(first.id)
    svc.execute(first.id)

    with pytest.raises(UnprocessableError):
        svc.create([package.id])


# --- success, partial failure, retry (Part 3) --------------------------------------------------


def test_partial_failure_is_reported_precisely_never_all_or_nothing(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    ok = make_approved_package(client, db_session, domain="ok.invalid", name="OK Corp")
    bad = make_approved_package(client, db_session, domain="bad.invalid", name="Bad Corp")
    provider = FakeMailProvider(
        [SentMessage(provider="fake", provider_message_id="id-1"), RuntimeError("boom")]
    )
    svc = service(db_session, settings, provider)
    batch = svc.create([ok.id, bad.id])
    svc.approve(batch.id)

    executed = svc.execute(batch.id)

    assert executed.status is SendBatchStatus.PARTIALLY_FAILED
    items = {item.application_package_id: item for item in svc.items(executed.id)}
    assert items[ok.id].status is SendBatchItemStatus.SENT
    assert items[bad.id].status is SendBatchItemStatus.FAILED
    assert items[bad.id].failure_reason == "provider_error"


def test_a_failed_item_can_be_retried_by_executing_again(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider([RuntimeError("network blip")])
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    first = svc.execute(batch.id)
    assert first.status is SendBatchStatus.PARTIALLY_FAILED

    second = svc.execute(batch.id)  # the SAME provider now succeeds by default

    (item,) = svc.items(second.id)
    assert item.status is SendBatchItemStatus.SENT
    assert item.attempts == 2
    assert second.status is SendBatchStatus.COMPLETED


# --- idempotent retry via find_existing (Part: reducing the Gmail double-send risk) ------------


def test_the_same_message_id_is_reused_across_retries(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeGmailLikeProvider([RuntimeError("network blip")])
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)  # fails once, but a message_id is generated and stored

    (item_after_first,) = svc.items(batch.id)
    assert item_after_first.message_id is not None
    first_message_id = item_after_first.message_id

    svc.execute(batch.id)  # retry: must reuse the SAME id, never regenerate

    (item_after_second,) = svc.items(batch.id)
    assert item_after_second.message_id == first_message_id
    assert all(m.message_id == first_message_id for m in provider.sent)


def test_a_retry_finds_the_message_already_sent_and_never_calls_send_again(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeGmailLikeProvider([RuntimeError("response lost after Google accepted it")])
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)
    (item,) = svc.items(batch.id)
    assert item.status is SendBatchItemStatus.FAILED
    assert item.message_id is not None
    # Simulate Gmail having actually sent it despite the lost response: it is now findable.
    provider.existing[item.message_id] = SentMessage(
        provider="gmail", provider_message_id="found-1", thread_id="thread-1"
    )

    executed = svc.execute(batch.id)

    (retried,) = svc.items(executed.id)
    assert retried.status is SendBatchItemStatus.SENT
    assert retried.provider_message_id == "found-1"
    assert retried.thread_id == "thread-1"
    assert len(provider.sent) == 1  # the actual send() was called only on the FIRST attempt
    assert provider.find_calls == [item.message_id]


def test_a_retry_with_nothing_found_sends_again_with_the_same_id(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeGmailLikeProvider([RuntimeError("network blip")])  # nothing in `existing`
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.SENT
    assert len(provider.sent) == 2  # first (failed) attempt + the real retry
    assert provider.find_calls  # it WAS checked before the retry, just found nothing


def test_find_existing_failing_never_blocks_the_retry(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    class FlakyFindExisting(FakeGmailLikeProvider):
        def find_existing(self, message_id: str) -> SentMessage | None:
            raise RuntimeError("Gmail search is down")

    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FlakyFindExisting([RuntimeError("network blip")])
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)

    executed = svc.execute(batch.id)  # find_existing raises, must still fall back to a real retry

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.SENT


def test_a_provider_without_find_existing_support_is_unaffected(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    """`FakeMailProvider` does not implement `find_existing`: retries must still work exactly as
    before (already covered by test_a_failed_item_can_be_retried_by_executing_again), and no
    exception is raised trying to use a capability it does not have."""
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider([RuntimeError("network blip")])
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)

    executed = svc.execute(batch.id)

    (item,) = svc.items(executed.id)
    assert item.status is SendBatchItemStatus.SENT


def test_a_sent_item_is_never_re_attempted_on_a_later_execute(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)

    with pytest.raises(ConflictError):  # a completed batch cannot be executed again
        svc.execute(batch.id)
    assert len(provider.sent) == 1


# --- idempotence / no double send (Part 9, 11) --------------------------------------------------


def test_never_sends_the_same_package_twice_across_two_different_batches(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    first_batch = svc.create([package.id])
    svc.approve(first_batch.id)
    svc.execute(first_batch.id)

    # A second batch containing the same package is refused outright at creation time.
    with pytest.raises(UnprocessableError):
        svc.create([package.id])
    assert len(provider.sent) == 1


def test_approving_a_batch_twice_is_refused(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())
    batch = svc.create([package.id])
    svc.approve(batch.id)

    with pytest.raises(ConflictError):
        svc.approve(batch.id)


def test_executing_an_unapproved_batch_is_refused(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())
    batch = svc.create([package.id])  # never approved

    with pytest.raises(ConflictError):
        svc.execute(batch.id)


# --- security ------------------------------------------------------------------------------


def test_no_smtp_import_anywhere_in_the_send_batch_or_gmail_modules() -> None:
    for path in (
        Path("app/services/send_batch.py"),
        Path("app/integrations/gmail/client.py"),
        Path("app/integrations/gmail/oauth.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "smtplib" not in source


def test_no_email_is_ever_invented_only_the_stored_channel_value_is_used(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    provider = FakeMailProvider()
    svc = service(db_session, settings, provider)
    batch = svc.create([package.id])
    svc.approve(batch.id)

    svc.execute(batch.id)

    (message,) = provider.sent
    assert message.to == "jamie@a.invalid"


# --- audit -----------------------------------------------------------------------------------


def test_audit_events_cover_the_full_batch_lifecycle_with_a_correlation_id(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    svc = service(db_session, settings, FakeMailProvider())
    batch = svc.create([package.id])
    svc.approve(batch.id)
    executed = svc.execute(batch.id)

    events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type.in_(
                [
                    AuditEventType.SEND_BATCH_CREATED,
                    AuditEventType.SEND_BATCH_APPROVED,
                    AuditEventType.SEND_REQUESTED,
                    AuditEventType.SEND_SENT,
                    AuditEventType.SEND_BATCH_COMPLETED,
                ]
            )
        )
    ).all()
    types = {e.event_type for e in events}
    assert types == {
        AuditEventType.SEND_BATCH_CREATED,
        AuditEventType.SEND_BATCH_APPROVED,
        AuditEventType.SEND_REQUESTED,
        AuditEventType.SEND_SENT,
        AuditEventType.SEND_BATCH_COMPLETED,
    }
    batch_level = {
        AuditEventType.SEND_BATCH_CREATED,
        AuditEventType.SEND_BATCH_APPROVED,
        AuditEventType.SEND_BATCH_COMPLETED,
    }
    correlation_ids = {
        e.details.get("correlation_id") for e in events if e.event_type in batch_level
    }
    assert correlation_ids == {executed.correlation_id}
    for event in events:
        for value in event.details.values():
            if isinstance(value, str):
                assert "@" not in value and len(value) <= 64


def test_no_secret_or_token_ever_appears_in_an_audit_detail(
    client: TestClient, db_session: Session, settings: Settings, cv: Path
) -> None:
    package = make_approved_package(client, db_session, domain="a.invalid", name="A")
    ingest_cv(db_session, candidate_id_of(db_session))
    svc = service(db_session, settings, FakeMailProvider())
    batch = svc.create([package.id])
    svc.approve(batch.id)
    svc.execute(batch.id)

    events = db_session.scalars(select(AuditEvent)).all()
    for event in events:
        dump = str(event.details)
        assert "token" not in dump.lower() and "refresh" not in dump.lower()


# --- no global score / ranking (Part 13) ---------------------------------------------------------


def test_send_batch_models_have_no_score_or_ranking_field() -> None:
    from app.models.send_batch import SendBatch as SendBatchModel
    from app.models.send_batch import SendBatchItem as SendBatchItemModel

    names = {c.name for c in SendBatchModel.__table__.columns}
    names |= {c.name for c in SendBatchItemModel.__table__.columns}
    assert not any(bad in name for name in names for bad in ("score", "rank"))
