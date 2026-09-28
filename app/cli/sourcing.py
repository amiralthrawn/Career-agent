"""`career-agent sourcing ...` - start a real search run and inspect past ones.

Uses the SAME `SourcingService` as the API (`app.api.sourcing`): SearchProfile, `HitExtractor`,
provenance, de-duplication and qualification are never bypassed. The `web` provider registry is
built the same way `app.api.sourcing.get_providers` builds it: empty unless Perplexity is
explicitly enabled (`RESEARCH_ENABLED`, `PERPLEXITY_PRESET`, the `perplexity_api_key` secret).
"""

import argparse
import json
import sys

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.secrets import (
    OPENROUTER_API_KEY,
    PERPLEXITY_API_KEY,
    SecretStoreError,
    get_secret_store,
)
from app.integrations.llm.openrouter import OpenRouterClient
from app.integrations.llm.ports import LLMClient
from app.integrations.research.perplexity import PerplexityClient
from app.integrations.research.ports import ResearchProvider
from app.integrations.sourcing.ports import SourcingProviders
from app.models import Campaign, SearchRun, SearchRunItem
from app.models.enums import CampaignStatus, SourcingMode
from app.schemas.campaign import CampaignCreate
from app.schemas.sourcing import SearchRunCreate
from app.services.campaign import CampaignService, RoundSummary
from app.services.sourcing import SourcingService, default_web_search_provider


def _providers() -> SourcingProviders:
    settings = get_settings()
    try:
        store = get_secret_store()
    except SecretStoreError:
        return SourcingProviders()
    provider = default_web_search_provider(settings, store)
    return SourcingProviders(web={"perplexity": provider} if provider else {})


def _research_provider() -> ResearchProvider | None:
    """The SAME capability gate as company/contact research: `None` unless already enabled."""
    settings = get_settings()
    if not settings.research_enabled or not settings.perplexity_preset:
        return None
    try:
        store = get_secret_store()
        if not store.exists(PERPLEXITY_API_KEY):
            return None
    except SecretStoreError:
        return None
    return PerplexityClient(store, settings.perplexity_preset)


def _llm_client() -> LLMClient | None:
    """The SAME capability gate as draft generation: `None` unless already enabled."""
    settings = get_settings()
    if not settings.llm_enabled or not settings.openrouter_model:
        return None
    try:
        store = get_secret_store()
        if not store.exists(OPENROUTER_API_KEY):
            return None
    except SecretStoreError:
        return None
    return OpenRouterClient(store, settings.openrouter_model)


def _print_run(run: SearchRun, items: list[SearchRunItem]) -> None:
    print(f"Run #{run.id} - {run.mode.value} via {run.provider} - {run.status.value}")
    print(f"  Query: {json.dumps(run.query)}")
    print(
        f"  results_raw={run.results_raw} targets_created={run.targets_created} "
        f"targets_existing={run.targets_existing} rejected={run.items_rejected} "
        f"errors={run.item_errors}"
    )
    if run.breakdown:
        for flow, counters in run.breakdown.items():
            print(
                f"  {flow}: created={counters['targets_created']} "
                f"existing={counters['targets_existing']}"
            )
    if run.errors:
        print(f"  errors: {run.errors}")
    if run.sources_consulted:
        print(f"  sources_consulted: {', '.join(run.sources_consulted)}")
    print("  Items:")
    for item in items:
        detail = (
            f"target=#{item.target_id}"
            if item.target_id is not None
            else f"reason={item.reason.value if item.reason else '?'}"
        )
        print(f"    [{item.position}] {item.outcome.value:<16} {detail}")


def _search(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        service = SourcingService(session, _providers())
        data = SearchRunCreate(
            mode=SourcingMode(args.mode),
            provider=args.provider,
            profile_id=args.profile,
            max_results=args.max_results,
        )
        run = service.run(data, actor="cli")
        _print_run(run, list(service.items(run.id)))
    return 0


def _runs(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        service = SourcingService(session, _providers())
        runs = service.list(profile_id=args.profile, limit=args.limit, offset=0)
        if not runs:
            print("(none)")
        for run in runs:
            print(
                f"#{run.id:<4} {run.mode.value:<10} {run.provider:<12} "
                f"{run.status.value:<20} results={run.results_raw:<3} "
                f"created={run.targets_created:<3} rejected={run.items_rejected}"
            )
    return 0


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        service = SourcingService(session, _providers())
        run = service.get(args.run_id)
        _print_run(run, list(service.items(run.id)))
    return 0


def _print_campaign(campaign: Campaign) -> None:
    print(f"Campaign #{campaign.id} - {campaign.mode.value} via {campaign.provider}")
    print(
        f"  Status: {campaign.status.value}"
        + (f" ({campaign.stop_reason})" if campaign.stop_reason else "")
    )
    print(
        f"  Calls: {campaign.calls_made}/{campaign.max_calls}   "
        f"Targets created: {campaign.targets_created}/{campaign.daily_target}   "
        f"Consecutive empty: {campaign.consecutive_empty_calls}/{campaign.max_consecutive_empty}"
    )
    print(
        f"  Requirements extracted: {campaign.requirements_extracted}   "
        f"Contacts proposed: {campaign.contacts_proposed}   "
        f"Drafts generated: {campaign.drafts_generated}   "
        f"Packages prepared: {campaign.packages_prepared}"
    )
    print(
        f"  Started: {campaign.started_at}"
        + (f"   Finished: {campaign.finished_at}" if campaign.finished_at else "")
    )


def _campaign_start(args: argparse.Namespace) -> int:
    llm = _llm_client()
    if llm is None:
        print(
            "note: no LLM configured (LLM_ENABLED/OPENROUTER_MODEL/openrouter_api_key) - "
            "sourcing/qualification/requirements/contacts will still run; no draft will be "
            "generated this campaign.",
            file=sys.stderr,
        )
    research = _research_provider()
    if research is None:
        print(
            "note: no research provider configured (RESEARCH_ENABLED/PERPLEXITY_PRESET/"
            "perplexity_api_key) - contact proposals will be skipped this campaign.",
            file=sys.stderr,
        )

    def on_round(campaign: Campaign, round_summary: RoundSummary) -> None:
        print(
            f"[call {campaign.calls_made}/{campaign.max_calls}] run #{round_summary.run_id}: "
            f"+{round_summary.new_targets} new target(s) (total {campaign.targets_created}/"
            f"{campaign.daily_target}, {campaign.consecutive_empty_calls} consecutive empty)"
        )

    with get_session_factory()() as session:
        service = CampaignService(
            session, _providers(), research_provider=research, llm=llm, github_username=None
        )
        data = CampaignCreate(
            profile_id=args.profile,
            mode=SourcingMode(args.mode),
            provider=args.provider,
            daily_target=args.target,
            max_calls=args.max_calls,
            max_duration_minutes=args.max_duration_minutes,
            max_results_per_call=args.max_results_per_call,
            max_consecutive_empty=args.max_consecutive_empty,
        )
        campaign = service.start(data, actor="cli", on_round=on_round)
        print()
        _print_campaign(campaign)
    return 0 if campaign.status is not CampaignStatus.FAILED else 1


def _campaign_show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        service = CampaignService(session, _providers())
        funnel = service.funnel(args.campaign_id)
        _print_campaign(service.get(args.campaign_id))
        print("\nFunnel (current state of everything this campaign found):")
        print(f"  Raw results total:        {funnel.raw_results_total}")
        print(f"  Targets total:            {funnel.targets_total}")
        print(
            f"  Qualified / Uncertain / Excluded: {funnel.qualified} / {funnel.uncertain} / "
            f"{funnel.excluded}"
        )
        print(f"  Contacts proposed (pending review): {funnel.contacts_proposed}")
        print(f"  Contacts accepted with an email:    {funnel.contacts_accepted_with_email}")
        for status, count in funnel.packages_by_status.items():
            print(f"  Packages {status.value}: {count}")
        print(f"  Packages sent: {funnel.packages_sent}")
    return 0


def _campaign_list(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        service = CampaignService(session, _providers())
        campaigns = service.list(limit=args.limit)
        if not campaigns:
            print("(none)")
        for campaign in campaigns:
            print(
                f"#{campaign.id:<4} {campaign.mode.value:<10} {campaign.status.value:<24} "
                f"targets={campaign.targets_created}/{campaign.daily_target} "
                f"calls={campaign.calls_made}/{campaign.max_calls}"
            )
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sourcing = subparsers.add_parser(
        "sourcing", help="Search for offers/companies and inspect runs"
    )
    sub = sourcing.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Start a search run")
    search.add_argument("--mode", choices=["offers", "companies", "all"], default="offers")
    search.add_argument("--provider", default="perplexity")
    search.add_argument("--profile", type=int, default=None)
    search.add_argument("--max-results", type=int, default=20, dest="max_results")
    search.set_defaults(handler=_search)

    runs = sub.add_parser("runs", help="List recent search runs")
    runs.add_argument("--profile", type=int, default=None)
    runs.add_argument("--limit", type=int, default=25)
    runs.set_defaults(handler=_runs)

    show = sub.add_parser("show", help="Show one search run and its items")
    show.add_argument("run_id", type=int)
    show.set_defaults(handler=_show)

    campaign = sub.add_parser(
        "campaign", help="Chain searches + enrichment toward a daily target (bounded, one process)"
    )
    campaign_sub = campaign.add_subparsers(dest="subcommand", required=True)

    campaign_start = campaign_sub.add_parser("start", help="Start a bounded campaign")
    campaign_start.add_argument("--mode", choices=["offers", "companies", "all"], default="all")
    campaign_start.add_argument("--provider", default="perplexity")
    campaign_start.add_argument("--profile", type=int, default=None)
    campaign_start.add_argument(
        "--target", type=int, default=500, help="Daily new-target objective"
    )
    campaign_start.add_argument("--max-calls", type=int, default=20)
    campaign_start.add_argument("--max-duration-minutes", type=int, default=60)
    campaign_start.add_argument("--max-results-per-call", type=int, default=20)
    campaign_start.add_argument("--max-consecutive-empty", type=int, default=3)
    campaign_start.set_defaults(handler=_campaign_start)

    campaign_show = campaign_sub.add_parser("show", help="Show a campaign's progress and funnel")
    campaign_show.add_argument("campaign_id", type=int)
    campaign_show.set_defaults(handler=_campaign_show)

    campaign_list = campaign_sub.add_parser("list", help="List campaigns")
    campaign_list.add_argument("--limit", type=int, default=25)
    campaign_list.set_defaults(handler=_campaign_list)
