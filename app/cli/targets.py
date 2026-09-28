"""`career-agent targets ...` - inspect targets (a company, with an optional offer)."""

import argparse

from app.core.database import get_session_factory
from app.models import Target
from app.services.targets import TargetService


def _line(target: Target) -> str:
    offer = target.opportunity.title if target.opportunity else "(spontaneous)"
    return (
        f"#{target.id:<5} {target.company.name:<25.25} {offer:<30.30} "
        f"{target.status.value:<12} {target.mode}"
    )


def _list(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        targets = TargetService(session).list_targets(limit=args.limit, offset=0)
        if not targets:
            print("(none)")
        for target in targets:
            print(_line(target))
    return 0


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        target = TargetService(session).get_target(args.target_id)
        print(f"Target #{target.id} ({target.mode})")
        print(f"  Company: {target.company.name} ({target.company.location or 'location -'})")
        if target.opportunity is not None:
            offer = target.opportunity
            remote = offer.remote_mode.value if offer.remote_mode else "-"
            print(f"  Offer: {offer.title}")
            print(f"  Location: {offer.location or '-'}  Remote: {remote}")
            print(f"  URL: {offer.url or '-'}")
        contract = target.contract_type.value if target.contract_type else "-"
        print(f"  Contract: {contract}")
        print(f"  Status: {target.status.value}")
        if target.dismissed_reason:
            print(f"  Dismissed reason: {target.dismissed_reason}")
        if target.relevance_note:
            print(f"  Relevance note: {target.relevance_note}")
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    targets = subparsers.add_parser("targets", help="Inspect targets")
    sub = targets.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="List targets")
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.set_defaults(handler=_list)

    show = sub.add_parser("show", help="Show one target")
    show.add_argument("target_id", type=int)
    show.set_defaults(handler=_show)
