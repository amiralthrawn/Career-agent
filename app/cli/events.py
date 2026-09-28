"""`career-agent events [--target ID] [--application ID]` - the operational journal.

Reads the SAME `ApplicationEvent` rows the API's per-package `.../events` route reads
(`app.services.application_tracking.ApplicationTrackingService.list_all_events`) - never a
second event store, and never a technical log: only real-world facts about a candidature.
"""

import argparse

from app.core.database import get_session_factory
from app.services.application_tracking import ApplicationTrackingService


def _run(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        events = ApplicationTrackingService(session).list_all_events(
            target_id=args.target, package_id=args.application, limit=args.limit
        )
        if not events:
            print("(none)")
        for event in events:
            note = f"  {event.note}" if event.note else ""
            print(
                f"{event.recorded_at:%Y-%m-%d %H:%M} app=#{event.application_package_id:<5} "
                f"{event.event_type.value:<20} {event.status.value:<10} "
                f"{event.origin.value:<8}{note}"
            )
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    events = subparsers.add_parser("events", help="Operational journal (application events)")
    events.add_argument("--target", type=int, default=None)
    events.add_argument("--application", type=int, default=None)
    events.add_argument("--limit", type=int, default=50)
    events.set_defaults(handler=_run)
