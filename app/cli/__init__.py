"""Career-agent CLI - the primary interface for step 3a onward (see docs/qualification_v1.md).

`career-agent <group> <command> ...` calls the SAME services the HTTP API uses (e.g.
`OpportunityQualificationService`): the CLI never re-implements a business rule, it only formats
input/output around an existing service - `CLI -> Service -> Repository -> SQLite`. No LLM, no
network call, no autonomous behaviour: every command does exactly what it is asked, once, and
stops (a `run` is idempotent - see `OpportunityQualificationService.run` - never a retry loop).

Usage: `python -m app.cli <args>`, or `career-agent <args>` once installed
(`pip install -e .` registers the `career-agent` console script; see `[project.scripts]` in
pyproject.toml). A database must already be configured (`DATABASE_URL`), exactly as for the API.
"""

import argparse
import sys
from collections.abc import Sequence

from app.cli import applications as applications_cli
from app.cli import brief as brief_cli
from app.cli import drafts as drafts_cli
from app.cli import events as events_cli
from app.cli import qualification as qualification_cli
from app.cli import requirements as requirements_cli
from app.cli import sourcing as sourcing_cli
from app.cli import status as status_cli
from app.cli import targets as targets_cli
from app.core.errors import DomainError

_MODULES = (
    status_cli,
    qualification_cli,
    sourcing_cli,
    targets_cli,
    requirements_cli,
    brief_cli,
    drafts_cli,
    applications_cli,
    events_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="career-agent", description="Career-agent CLI")
    subparsers = parser.add_subparsers(dest="group", required=True)
    for module in _MODULES:
        module.add_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:  # a group with no command was given
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except DomainError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
