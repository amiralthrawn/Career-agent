"""`career-agent qualification ...` and `career-agent opportunities ...` (step 3a).

Both groups call the SAME `OpportunityQualificationService` the API uses (see
`app.services.opportunity_qualification`, `app.api.opportunity_qualification`): no business rule
is duplicated here, only terminal formatting.
"""

import argparse

from app.core.database import get_session_factory
from app.schemas.opportunity_qualification import V1OpportunitySummary, V1QualificationRead
from app.services.opportunity_qualification import OpportunityQualificationService

# ✓ satisfied/qualified · — excluded/incompatible/not_qualified · ? uncertain/open question ·
# ★ a priority (or IDF) location tier. A plain space keeps columns aligned when no symbol applies.
DECISION_SYMBOLS = {"qualified": "✓", "not_qualified": "—", "uncertain": "?"}
OUTCOME_SYMBOLS = {"satisfied": "✓", "incompatible": "—", "not_matched": "?", "unknown": "?"}
TIER_SYMBOLS = {"priority": "★", "idf": "★"}


def _tier_symbol(tier: str) -> str:
    return TIER_SYMBOLS.get(tier, " ")


def _print_qualification(result: V1QualificationRead) -> None:
    symbol = DECISION_SYMBOLS[result.decision]
    print(f"Target #{result.target_id}")
    print(
        f"Decision:     {symbol} {result.decision.upper()}  (criteria: {result.criteria_version})"
    )
    matched = result.role_family_matched_term or "none recognised"
    print(f"Role family:  {result.role_family.value} (matched: {matched})")
    print(f"Location:     {_tier_symbol(result.location_tier.value)} {result.location_tier.value}")
    print(f"Stale:        {'yes' if result.stale else 'no'}")
    print("Reasons:")
    for reason in result.reasons:
        symbol = OUTCOME_SYMBOLS[reason.result]
        shown = reason.expected[:5]
        expected = ", ".join(shown) + (", ..." if len(reason.expected) > len(shown) else "")
        actual = reason.actual if reason.actual is not None else "—"
        print(
            f"  {symbol} {reason.criterion:<15} {reason.result:<13} "
            f"expected=[{expected}] actual={actual}"
        )


def _run(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        outcome = OpportunityQualificationService(session).run(args.target_id)
        _print_qualification(outcome.qualification)
        print(f"({'created' if outcome.created else 'unchanged: same inputs'})")
    return 0


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        _print_qualification(OpportunityQualificationService(session).show(args.target_id))
    return 0


def _print_summary_table(rows: list[V1OpportunitySummary]) -> None:
    if not rows:
        print("(none)")
        return
    for row in rows:
        symbol = DECISION_SYMBOLS[row.decision]
        title = row.offer_title or "(spontaneous)"
        print(
            f"#{row.target_id:<5} {row.company_name:<25.25} {title:<30.30} "
            f"{symbol} {row.decision:<12} {row.role_family.value:<20} "
            f"{_tier_symbol(row.location_tier.value)} {row.location_tier.value}"
        )


def _qualified(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        _print_summary_table(list(OpportunityQualificationService(session).list_qualified()))
    return 0


def _uncertain(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        _print_summary_table(list(OpportunityQualificationService(session).list_uncertain()))
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    qualification = subparsers.add_parser(
        "qualification", help="Run/show a target's Criteria v1 qualification"
    )
    qualification_sub = qualification.add_subparsers(dest="command", required=True)

    run = qualification_sub.add_parser("run", help="Qualify a target against Criteria v1")
    run.add_argument("target_id", type=int)
    run.set_defaults(handler=_run)

    show = qualification_sub.add_parser("show", help="Show a target's current qualification")
    show.add_argument("target_id", type=int)
    show.set_defaults(handler=_show)

    opportunities = subparsers.add_parser(
        "opportunities", help="List opportunities by their Criteria v1 decision"
    )
    opportunities_sub = opportunities.add_subparsers(dest="command", required=True)

    qualified = opportunities_sub.add_parser("qualified", help="List qualified opportunities")
    qualified.set_defaults(handler=_qualified)

    uncertain = opportunities_sub.add_parser("uncertain", help="List uncertain opportunities")
    uncertain.set_defaults(handler=_uncertain)
