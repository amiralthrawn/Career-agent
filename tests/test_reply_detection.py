"""SimulatedReplyDetectionProvider (step 11 preparation): a fake, for tests only. No real Gmail
reply detection exists yet - see docs/application_tracking.md for the scope this would need.
"""

from datetime import UTC, datetime, timedelta

from app.integrations.gmail.reply_detection import ReplyEvidence, SimulatedReplyDetectionProvider
from app.models.enums import ApplicationEventType

NOW = datetime.now(UTC)


def test_filters_by_thread_id() -> None:
    provider = SimulatedReplyDetectionProvider(
        [
            ReplyEvidence(message_id="m1", thread_id="t1", received_at=NOW),
            ReplyEvidence(message_id="m2", thread_id="t2", received_at=NOW),
        ]
    )

    found = provider.detect_replies(["t1"], since=NOW - timedelta(days=1))

    assert [e.message_id for e in found] == ["m1"]


def test_filters_by_since() -> None:
    provider = SimulatedReplyDetectionProvider(
        [
            ReplyEvidence(message_id="old", thread_id="t1", received_at=NOW - timedelta(days=10)),
            ReplyEvidence(message_id="new", thread_id="t1", received_at=NOW),
        ]
    )

    found = provider.detect_replies(["t1"], since=NOW - timedelta(days=1))

    assert [e.message_id for e in found] == ["new"]


def test_records_every_call_for_inspection() -> None:
    provider = SimulatedReplyDetectionProvider([])

    provider.detect_replies(["t1", "t2"], since=NOW)

    assert provider.calls == [(("t1", "t2"), NOW)]


def test_no_real_network_or_gmail_client_import() -> None:
    from pathlib import Path

    source = Path("app/integrations/gmail/reply_detection.py").read_text(encoding="utf-8")
    for banned in ("http.client", "HTTPSConnection", "smtplib", "requests", "httpx"):
        assert banned not in source


def test_suggested_type_is_a_guess_never_a_decision() -> None:
    evidence = ReplyEvidence(
        message_id="m1",
        thread_id="t1",
        received_at=NOW,
        suggested_type=ApplicationEventType.OFFER_RECEIVED,
    )

    # The dataclass has no field for confidence beyond what the CONSUMER (application_tracking)
    # assigns: it is never itself a confirmed `InfoStatus`.
    assert not hasattr(evidence, "status")
    assert not hasattr(evidence, "confirmed")
