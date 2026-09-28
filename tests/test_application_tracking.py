"""Application lifecycle tracking (step 11): system-recorded facts, manual entry, corrections,
detected (simulated) events, follow-up candidates. All synthetic, no network, no real Gmail call.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import UnprocessableError
from app.integrations.gmail.reply_detection import ReplyEvidence
from app.models import (
    ApplicationEvent,
    ApplicationPackage,
    Contact,
    ContactChannel,
    DocumentIngestion,
    Target,
    TargetContact,
)
from app.models.enums import (
    ApplicationEventOrigin,
    ApplicationEventType,
    ChannelKind,
    InfoStatus,
)
from app.schemas.application_package import ApplicationPrepareRequest
from app.services.application_package import ApplicationPackageService
from app.services.application_tracking import ApplicationTrackingService
from tests import targets_factory as f
from tests.docx_factory import SYNTHETIC_CV_LINES, simple_docx
from tests.llm_fakes import FakeLLMClient
from tests.qualification_factory import add_target, crit, make_profile, qualify
from tests.requirements_factory import extract


@pytest.fixture
def settings(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SEND_MODE", "dry_run")
    monkeypatch.setenv("MAIL_FROM", "sender.fixture@example.invalid")
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


def make_package(
    client: TestClient,
    db_session: Session,
    *,
    domain: str = "a.invalid",
    name: str = "A",
    approve: bool = True,
) -> ApplicationPackage:
    make_profile(
        client,
        [crit("contract_type", ["apprenticeship"], "required")],
        name=f"Fixture profile {domain}",
    )
    target_json = add_target(client, domain=domain, name=name)
    extract(client, target_json["id"])
    assert qualify(client, target_json["id"]).status_code in (200, 201)
    target = db_session.get(Target, target_json["id"])
    assert target is not None
    accept_contact(db_session, target)
    packages = ApplicationPackageService(db_session, FakeLLMClient(), None, None)
    package = packages.prepare(target.id, ApplicationPrepareRequest())
    if approve:
        package = packages.decide(package.id, approve=True)
    return package


def tracking(session: Session) -> ApplicationTrackingService:
    return ApplicationTrackingService(session)


# --- system-recorded facts (Part 2/5: never fabricated, always certain) -----------------------


def test_preparing_a_package_records_a_prepared_event(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))

    package = make_package(client, db_session, approve=False)

    (event,) = tracking(db_session).list_events(package.id)
    assert event.event_type is ApplicationEventType.PREPARED
    assert event.origin is ApplicationEventOrigin.SYSTEM
    assert event.status is InfoStatus.FOUND


def test_approving_a_package_records_an_approved_event(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))

    package = make_package(client, db_session, approve=True)

    events = tracking(db_session).list_events(package.id)
    types = {e.event_type for e in events}
    assert ApplicationEventType.PREPARED in types
    assert ApplicationEventType.APPROVED in types


def test_rejecting_a_package_records_no_lifecycle_event(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session, approve=False)
    packages = ApplicationPackageService(db_session, None, None, None)

    packages.decide(package.id, approve=False)

    events = tracking(db_session).list_events(package.id)
    assert all(e.event_type is not ApplicationEventType.APPROVED for e in events)


# --- manual entry (Part 4) --------------------------------------------------------------------


def test_manual_entry_records_a_confirmed_response_by_default(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)

    event = tracking(db_session).record_manual_event(
        package.id, ApplicationEventType.RESPONSE_RECEIVED, datetime.now(UTC)
    )

    assert event.origin is ApplicationEventOrigin.MANUAL
    assert event.status is InfoStatus.FOUND


def test_manual_entry_can_be_marked_uncertain_by_the_human_themselves(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)

    event = tracking(db_session).record_manual_event(
        package.id,
        ApplicationEventType.REJECTED,
        datetime.now(UTC),
        status=InfoStatus.UNCERTAIN,
        note="ambiguous e-mail, not sure it's a rejection",
    )

    assert event.status is InfoStatus.UNCERTAIN


@pytest.mark.parametrize(
    "event_type",
    [ApplicationEventType.PREPARED, ApplicationEventType.APPROVED, ApplicationEventType.SENT],
)
def test_manual_entry_refuses_system_only_types(
    client: TestClient, db_session: Session, cv: Path, event_type: ApplicationEventType
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)

    with pytest.raises(UnprocessableError):
        tracking(db_session).record_manual_event(package.id, event_type, datetime.now(UTC))


def test_manual_entry_with_the_same_reference_is_idempotent(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    svc = tracking(db_session)

    first = svc.record_manual_event(
        package.id, ApplicationEventType.REJECTED, datetime.now(UTC), reference="ref-1"
    )
    second = svc.record_manual_event(
        package.id, ApplicationEventType.REJECTED, datetime.now(UTC), reference="ref-1"
    )

    assert first.id == second.id
    # prepared + approved (make_package's default) + this one rejection, never duplicated
    assert len(svc.list_events(package.id)) == 3


# --- detected (Gmail-simulated) events (Part 5) -------------------------------------------------


def test_detected_events_are_always_uncertain(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    evidence = [
        ReplyEvidence(
            message_id="msg-1",
            thread_id="thread-1",
            received_at=datetime.now(UTC),
            suggested_type=ApplicationEventType.INTERVIEW_PROPOSED,
        )
    ]

    (event,) = tracking(db_session).record_detected_events(package.id, evidence)

    assert event.origin is ApplicationEventOrigin.GMAIL
    assert event.status is InfoStatus.UNCERTAIN
    assert event.event_type is ApplicationEventType.INTERVIEW_PROPOSED


def test_detected_event_without_a_classification_falls_back_to_response_received(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    evidence = [
        ReplyEvidence(message_id="msg-2", thread_id="thread-1", received_at=datetime.now(UTC))
    ]

    (event,) = tracking(db_session).record_detected_events(package.id, evidence)

    assert event.event_type is ApplicationEventType.RESPONSE_RECEIVED


def test_the_same_detected_message_is_never_recorded_twice(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    evidence = [
        ReplyEvidence(
            message_id="msg-3",
            thread_id="thread-1",
            received_at=datetime.now(UTC),
            suggested_type=ApplicationEventType.REJECTED,
        )
    ]
    svc = tracking(db_session)

    first = svc.record_detected_events(package.id, evidence)
    second = svc.record_detected_events(package.id, evidence)

    assert len(first) == 1 and second == []


# --- correction (Part 2: traceable, never silently erases history) -----------------------------


def test_correcting_an_event_creates_a_new_row_never_edits_the_original(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    svc = tracking(db_session)
    original = svc.record_manual_event(
        package.id, ApplicationEventType.REJECTED, datetime.now(UTC), note="typo: not a rejection"
    )

    corrected = svc.correct_event(original.id, event_type=ApplicationEventType.RESPONSE_RECEIVED)

    assert corrected.id != original.id
    assert corrected.corrected_event_id == original.id
    db_session.expire_all()
    still_there = db_session.get(ApplicationEvent, original.id)
    assert still_there is not None and still_there.event_type is ApplicationEventType.REJECTED


def test_confirming_an_uncertain_event_is_a_correction_that_only_changes_status(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    svc = tracking(db_session)
    evidence = [
        ReplyEvidence(
            message_id="msg-4",
            thread_id="thread-1",
            received_at=datetime.now(UTC),
            suggested_type=ApplicationEventType.OFFER_RECEIVED,
        )
    ]
    (detected,) = svc.record_detected_events(package.id, evidence)
    assert detected.status is InfoStatus.UNCERTAIN

    confirmed = svc.correct_event(detected.id, status=InfoStatus.FOUND)

    assert confirmed.status is InfoStatus.FOUND
    assert confirmed.event_type is ApplicationEventType.OFFER_RECEIVED
    assert confirmed.corrected_event_id == detected.id


def test_a_correction_cannot_fabricate_a_system_only_fact(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    svc = tracking(db_session)
    manual = svc.record_manual_event(
        package.id, ApplicationEventType.RESPONSE_RECEIVED, datetime.now(UTC)
    )

    with pytest.raises(UnprocessableError):
        svc.correct_event(manual.id, event_type=ApplicationEventType.SENT)


def test_the_event_row_is_immutable(client: TestClient, db_session: Session, cv: Path) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    event = tracking(db_session).record_manual_event(
        package.id, ApplicationEventType.RESPONSE_RECEIVED, datetime.now(UTC)
    )

    event.note = "tampered"
    with pytest.raises(Exception):  # noqa: B017 - the DB trigger raises a backend-specific error
        db_session.commit()
    db_session.rollback()


# --- follow-up candidates (Part 4) --------------------------------------------------------------


def test_needing_follow_up_lists_a_sent_package_with_no_response(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    old = datetime.now(UTC) - timedelta(days=20)
    svc = tracking(db_session)
    from app.services.application_tracking import record_system_event

    record_system_event(
        db_session, package.candidate_id, package.id, ApplicationEventType.SENT, occurred_at=old
    )
    db_session.commit()

    due = svc.needing_follow_up(days_since_sent=14)

    assert [p.id for p in due] == [package.id]


def test_needing_follow_up_excludes_a_package_with_a_confirmed_response(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    old = datetime.now(UTC) - timedelta(days=20)
    svc = tracking(db_session)
    from app.services.application_tracking import record_system_event

    record_system_event(
        db_session, package.candidate_id, package.id, ApplicationEventType.SENT, occurred_at=old
    )
    svc.record_manual_event(package.id, ApplicationEventType.REJECTED, datetime.now(UTC))

    due = svc.needing_follow_up(days_since_sent=14)

    assert due == []


def test_needing_follow_up_excludes_a_package_not_sent_long_enough_ago(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    from app.services.application_tracking import record_system_event

    record_system_event(
        db_session, package.candidate_id, package.id, ApplicationEventType.SENT
    )  # sent just now
    db_session.commit()

    due = tracking(db_session).needing_follow_up(days_since_sent=14)

    assert due == []


def test_an_uncertain_response_never_excludes_a_package_from_follow_up(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    """Part 2: never treat an unconfirmed detection as sufficient evidence a response happened."""
    ingest_cv(db_session, candidate_id_of(db_session))
    package = make_package(client, db_session)
    old = datetime.now(UTC) - timedelta(days=20)
    svc = tracking(db_session)
    from app.services.application_tracking import record_system_event

    record_system_event(
        db_session, package.candidate_id, package.id, ApplicationEventType.SENT, occurred_at=old
    )
    evidence = [
        ReplyEvidence(
            message_id="msg-5",
            thread_id="t",
            received_at=datetime.now(UTC),
            suggested_type=ApplicationEventType.RESPONSE_RECEIVED,
        )
    ]
    svc.record_detected_events(package.id, evidence)  # UNCERTAIN, never confirmed

    due = svc.needing_follow_up(days_since_sent=14)

    assert [p.id for p in due] == [package.id]  # still needs a real follow-up decision


# --- filtering (Part 4) -------------------------------------------------------------------------


def test_list_packages_filters_by_confirmed_event_type(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    rejected = make_package(client, db_session, domain="rejected.invalid", name="Rejected Co")
    other = make_package(client, db_session, domain="other.invalid", name="Other Co")
    svc = tracking(db_session)
    svc.record_manual_event(rejected.id, ApplicationEventType.REJECTED, datetime.now(UTC))

    matches = svc.list_packages(event_type=ApplicationEventType.REJECTED)

    assert [p.id for p in matches] == [rejected.id]
    assert other.id not in [p.id for p in matches]


def test_list_packages_with_no_filter_returns_everything(
    client: TestClient, db_session: Session, cv: Path
) -> None:
    ingest_cv(db_session, candidate_id_of(db_session))
    a = make_package(client, db_session, domain="a2.invalid", name="A2")
    b = make_package(client, db_session, domain="b2.invalid", name="B2")

    all_packages = tracking(db_session).list_packages()

    assert {a.id, b.id} <= {p.id for p in all_packages}


# --- no automatic send/follow-up (Part 4) --------------------------------------------------------


def test_no_automatic_send_or_follow_up_anywhere_in_this_module() -> None:
    from pathlib import Path as P

    source = P("app/services/application_tracking.py").read_text(encoding="utf-8")
    for banned in ("import MailSender", "import SendBatchService", "smtplib", ".send("):
        assert banned not in source
