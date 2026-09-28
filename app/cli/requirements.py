"""`career-agent requirements show <target_id>` - a target's extracted/manual requirements."""

import argparse

from app.core.database import get_session_factory
from app.services.requirements import RequirementService


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        requirements = RequirementService(session).list(args.target_id)
        if not requirements:
            print(
                "(none - run `career-agent requirements extract` via the API, or add one manually)"
            )
        for requirement in requirements:
            print(
                f"#{requirement.id:<5} {requirement.kind.value:<10} {requirement.label:<30.30} "
                f"{requirement.importance.value:<12} origin={requirement.origin.value}"
            )
            print(f"      excerpt: {requirement.excerpt[:120]!r}")
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    requirements = subparsers.add_parser("requirements", help="Inspect a target's requirements")
    sub = requirements.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Show a target's requirements")
    show.add_argument("target_id", type=int)
    show.set_defaults(handler=_show)
