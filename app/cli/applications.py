"""`career-agent applications ...` - list/show application packages (step 9) and their history.

Read-only: preparing, approving, rejecting and sending a package stay API actions
(`POST /api/applications/...`) - this CLI is for inspection, per the pipeline's own
human-in-the-loop design (a send is never triggered from a passive listing command).
"""

import argparse

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models import ApplicationPackage
from app.services.application_package import ApplicationPackageService
from app.services.application_tracking import ApplicationTrackingService
from app.services.targets import TargetService


def _line(package: ApplicationPackage) -> str:
    decided = package.decided_at.strftime("%Y-%m-%d") if package.decided_at else "-"
    return (
        f"#{package.id:<5} target=#{package.target_id:<5} {package.status.value:<18} "
        f"decided={decided}"
    )


def _list(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        packages = ApplicationTrackingService(session).list_packages()
        if not packages:
            print("(none)")
        for package in list(packages)[: args.limit]:
            print(_line(package))
    return 0


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        settings = get_settings()
        service = ApplicationPackageService(session, None, None, settings.github_username)
        package = service.get(args.package_id)
        target = TargetService(session).get_target(package.target_id)

        print(f"Application #{package.id} - target #{package.target_id} ({target.company.name})")
        if target.opportunity is not None:
            print(f"Offer: {target.opportunity.title}")
        print(f"Status: {package.status.value}  stale={service.is_stale(package)}")
        print(f"Draft: {package.draft_id or '-'}   CV: {service.cv_source_uri(package) or '-'}")
        if package.contact_id is not None:
            print(f"Contact: #{package.contact_id}")
        if package.warnings:
            print("Warnings:")
            for warning in package.warnings:
                print(f"  - [{warning['code']}] {warning['text']}")

        events = ApplicationTrackingService(session).list_events(package.id)
        if events:
            print("Events:")
            for event in events:
                print(
                    f"  {event.occurred_at:%Y-%m-%d} {event.event_type.value} "
                    f"({event.status.value}, {event.origin.value})"
                )
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    applications = subparsers.add_parser("applications", help="List/show application packages")
    sub = applications.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="List application packages")
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.set_defaults(handler=_list)

    show = sub.add_parser("show", help="Show one application package")
    show.add_argument("package_id", type=int)
    show.set_defaults(handler=_show)
