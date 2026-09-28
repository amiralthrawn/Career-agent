"""`career-agent brief show <target_id>` - the PersonalizationBrief for a target.

Computed on demand, exactly like the API route (`GET /api/targets/{id}/personalization-brief`):
nothing here is stored, nothing is sent. An out-of-date qualification is shown, not hidden -
`stale=true` is printed so a human (or the future draft generator) knows to re-qualify first.
"""

import argparse

from app.core.database import get_session_factory
from app.services.personalization import PersonalizationService


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        brief = PersonalizationService(session).brief(args.target_id)
        target = brief.target
        print(f"Target #{target.target_id} ({target.mode}) - {target.company.name}")
        if target.offer is not None:
            print(f"Offer: {target.offer.title}")
        print(
            f"Qualification: {brief.qualification.status.value}  stale={brief.qualification.stale}"
        )

        print(f"\nStrengths ({len(brief.strengths)}):")
        for strength in brief.strengths:
            print(f"  + {strength.requirement.label}")

        print(f"\nDo not claim ({len(brief.do_not_claim)}):")
        for item in brief.do_not_claim:
            print(f"  - {item.requirement.label} ({item.status.value})")

        print(f"\nOpen questions ({len(brief.open_questions)}):")
        for question in brief.open_questions:
            print(f"  ? {question.text}")

        print(f"\nEmphasis candidates ({len(brief.emphasis_candidates)}):")
        for candidate in brief.emphasis_candidates:
            print(f"  {candidate.rank}. {candidate.fact.name} (covers {candidate.covered_count})")
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    brief = subparsers.add_parser("brief", help="Show a target's PersonalizationBrief")
    sub = brief.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Show the brief")
    show.add_argument("target_id", type=int)
    show.set_defaults(handler=_show)
