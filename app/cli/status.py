"""`career-agent status` - an operational overview of the whole pipeline.

Reads the SAME tables/services every other command uses (sourcing runs, targets, drafts,
applications, the audit trail): never a second, parallel statistics system.
"""

import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.core.errors import NotFoundError
from app.integrations.sourcing.ports import SourcingProviders
from app.models import AuditEvent, Target
from app.models.enums import ApplicationEventType, ApplicationPackageStatus, DraftStatus
from app.repositories import candidate_brain as brain_repo
from app.services.application_tracking import ApplicationTrackingService
from app.services.draft_generation import DraftService
from app.services.search_profiles import SearchProfileService
from app.services.sourcing import SourcingService
from app.services.targets import TargetService

MAX_COUNT = 500  # a pragmatic cap for a personal-scale tool; noted, never silently exceeded


def _label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _print_last_sourcing_run(session: Session) -> None:
    runs = SourcingService(session, SourcingProviders()).list(profile_id=None, limit=1, offset=0)
    print("\nLast sourcing run")
    if not runs:
        print("  (none yet)")
        return
    run = runs[0]
    print(f"  Provider: {run.provider}")
    print(f"  Mode: {run.mode.value}")
    print(f"  Status: {run.status.value}")
    print(f"  Results: {run.results_raw}")
    print(f"  Targets created: {run.targets_created}")
    print(f"  Rejected: {run.items_rejected}")


def _print_targets(session: Session, candidate_id: int) -> None:
    total = (
        session.scalar(
            select(func.count()).select_from(Target).where(Target.candidate_id == candidate_id)
        )
        or 0
    )
    print("\nTargets")
    print(f"  Total: {total}")
    try:
        profile_id = SearchProfileService(session).active_profile().id
    except NotFoundError:
        print("  (no active search profile yet - qualification breakdown unavailable)")
        return
    targets = TargetService(session)
    for label, value in (
        ("Qualified", "candidate"),
        ("Uncertain", "needs_information"),
        ("Excluded", "excluded"),
    ):
        count = len(
            targets.list_targets(
                qualification=value, qualification_profile_id=profile_id, limit=MAX_COUNT
            )
        )
        suffix = "+" if count == MAX_COUNT else ""
        print(f"  {label}: {count}{suffix}")


def _print_drafts(session: Session) -> None:
    drafts = DraftService(session, None)
    print("\nDrafts")
    for status in (DraftStatus.PROPOSED, DraftStatus.APPROVED, DraftStatus.REJECTED):
        count = len(drafts.list(status=status, limit=MAX_COUNT))
        suffix = "+" if count == MAX_COUNT else ""
        print(f"  {_label(status.value)}: {count}{suffix}")


def _print_applications(session: Session) -> None:
    tracking = ApplicationTrackingService(session)
    by_status: dict[str, int] = {}
    for package in tracking.list_packages():
        by_status[package.status.value] = by_status.get(package.status.value, 0) + 1
    sent = len(tracking.list_packages(event_type=ApplicationEventType.SENT))
    print("\nApplications")
    for status in ApplicationPackageStatus:
        print(f"  {_label(status.value)}: {by_status.get(status.value, 0)}")
    print(f"  Sent: {sent}")


def _print_last_activity(session: Session) -> None:
    events = session.scalars(
        select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(5)
    ).all()
    print("\nLast activity")
    if not events:
        print("  (none yet)")
        return
    for event in events:
        print(f"  {event.occurred_at:%Y-%m-%d %H:%M} {event.event_type.value} ({event.actor})")


def _run(_args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        candidate = brain_repo.get_first_candidate(session)
        if candidate is None:
            print("No candidate created yet (POST /api/candidate or ingest a CV first).")
            return 0

        print("CAREER-AGENT")
        print("-" * 28)
        _print_last_sourcing_run(session)
        _print_targets(session, candidate.id)
        _print_drafts(session)
        _print_applications(session)
        _print_last_activity(session)
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    status = subparsers.add_parser("status", help="Operational overview of the whole pipeline")
    status.set_defaults(handler=_run)
