"""CampaignService: chains as many `SourcingService.run()` rounds as needed toward a daily
target, inside ONE bounded, human-started process - see `app.models.campaign` for why this is the
one deliberate exception to this project's "no autonomous agent" rule, and what stays excluded.

Each round is an ordinary sourcing run (no second engine), followed by best-effort enrichment of
whatever NEW targets that round produced, reusing the existing, unmodified services exactly as a
human would call them one at a time:

    SourcingService.run()  (unchanged: dedup, provenance, qualification)
        -> RequirementService.extract() + QualificationService.qualify() (re-qualify: 3b changed
           the fingerprint - the SAME rule a human follows, see docs/requirements.md)
        -> ContactResearchBatchService.run() (budget-limited, PROPOSES observations - a human
           still accepts/rejects them; nothing here trusts an address on its own)
        -> ApplicationPackageService.prepare() (only if an LLM is configured; a package this
           produces can reach `pending_validation` at most - approving or sending stays a
           SEPARATE, human-triggered action; this service NEVER calls `.decide()` or a
           `SendBatchService`)

A round's enrichment is best-effort per target: a `DomainError` on one target (e.g. a stale
qualification, a missing CV) is skipped, never aborts the round or the campaign.

Stops at the FIRST of: the daily target reached, a configured call/duration safety limit, too many
consecutive empty searches, or a provider failure - always recorded as `status`/`stop_reason`, and
progress is committed after EVERY round so a concurrent read sees it live.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError, NotFoundError
from app.integrations.github.ports import GitHubProvider
from app.integrations.llm.ports import LLMClient
from app.integrations.research.ports import ResearchProvider
from app.integrations.sourcing.ports import SourcingProviders
from app.models import Campaign, Contact, ContactChannel, SearchRun
from app.models.enums import (
    ApplicationEventType,
    ApplicationPackageStatus,
    CampaignStatus,
    ChannelKind,
    QualificationStatus,
    SearchRunStatus,
)
from app.repositories import application_event as event_repo
from app.repositories import campaign as repo
from app.repositories import candidate_brain as brain_repo
from app.repositories.targets import stage
from app.schemas.application_package import ApplicationPrepareRequest
from app.schemas.campaign import CampaignCreate, CampaignFunnel, CampaignRead
from app.schemas.sourcing import SearchRunCreate
from app.services.application_package import ApplicationPackageService
from app.services.contact_research_batch import DEFAULT_ROLE_CATEGORIES, ContactResearchBatchService
from app.services.qualification import QualificationService
from app.services.requirements import RequirementService
from app.services.search_profiles import SearchProfileService
from app.services.sourcing import SourcingService

MAX_PAGE = 100
# One search of contacts per newly-created, non-excluded target per round: generous enough for a
# real round (a handful to a few dozen new targets), never unbounded.
MAX_CONTACT_CALLS_PER_ROUND = 20

RoundCallback = Callable[[Campaign, "RoundSummary"], None]


class RoundSummary:
    def __init__(self, run_id: int, new_targets: int) -> None:
        self.run_id = run_id
        self.new_targets = new_targets


class CampaignService:
    def __init__(
        self,
        session: Session,
        providers: SourcingProviders,
        *,
        research_provider: ResearchProvider | None = None,
        llm: LLMClient | None = None,
        github: GitHubProvider | None = None,
        github_username: str | None = None,
    ) -> None:
        self._session = session
        self._sourcing = SourcingService(session, providers)
        self._requirements = RequirementService(session)
        self._qualification = QualificationService(session)
        self._contacts = ContactResearchBatchService(session, research_provider)
        self._llm = llm
        self._packages = ApplicationPackageService(session, llm, github, github_username)

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- run --------------------------------------------------------------------------------

    def start(
        self, data: CampaignCreate, *, actor: str = "cli", on_round: RoundCallback | None = None
    ) -> Campaign:
        profile = (
            SearchProfileService(self._session).get_profile(data.profile_id)
            if data.profile_id is not None
            else SearchProfileService(self._session).active_profile()
        )
        campaign = stage(
            self._session,
            Campaign(
                candidate_id=self._candidate_id(),
                profile_id=profile.id,
                mode=data.mode,
                provider=data.provider,
                max_results_per_call=data.max_results_per_call,
                daily_target=data.daily_target,
                max_calls=data.max_calls,
                max_duration_minutes=data.max_duration_minutes,
                max_consecutive_empty=data.max_consecutive_empty,
                status=CampaignStatus.RUNNING,
            ),
        )
        self._session.commit()
        deadline = time.monotonic() + data.max_duration_minutes * 60

        while True:
            stop = self._stop_condition(campaign, deadline)
            if stop is not None:
                self._finish(campaign, stop[0], stop[1])
                break

            run = self._sourcing.run(
                SearchRunCreate(
                    mode=data.mode,
                    provider=data.provider,
                    profile_id=profile.id,
                    max_results=data.max_results_per_call,
                ),
                actor=actor,
            )
            run.campaign_id = campaign.id
            campaign.calls_made += 1
            self._session.commit()  # the link and the call count survive even if enrichment fails

            if run.status is SearchRunStatus.FAILED:
                self._finish(
                    campaign, CampaignStatus.STOPPED_PROVIDER_ERROR, "provider_call_failed"
                )
                break

            new_target_ids = [
                item.target_id
                for item in self._sourcing.items(run.id)
                if item.target_id is not None and item.outcome.value == "target_created"
            ]
            campaign.consecutive_empty_calls = (
                0 if new_target_ids else campaign.consecutive_empty_calls + 1
            )
            campaign.targets_created += len(new_target_ids)

            self._enrich(new_target_ids, profile.id, campaign, actor)
            self._session.commit()
            if on_round is not None:
                on_round(campaign, RoundSummary(run.id, len(new_target_ids)))

        return campaign

    def _stop_condition(
        self, campaign: Campaign, deadline: float
    ) -> tuple[CampaignStatus, str] | None:
        if campaign.targets_created >= campaign.daily_target:
            return CampaignStatus.COMPLETED, "daily_target_reached"
        if campaign.calls_made >= campaign.max_calls:
            return CampaignStatus.STOPPED_CALL_LIMIT, "max_calls_reached"
        if time.monotonic() >= deadline:
            return CampaignStatus.STOPPED_DURATION_LIMIT, "max_duration_reached"
        if campaign.consecutive_empty_calls >= campaign.max_consecutive_empty:
            return (
                CampaignStatus.STOPPED_NO_NEW_RESULTS,
                f"{campaign.max_consecutive_empty}_consecutive_empty_searches",
            )
        return None

    def _finish(self, campaign: Campaign, status: CampaignStatus, reason: str) -> None:
        campaign.status = status
        campaign.stop_reason = reason
        campaign.finished_at = datetime.now(UTC)
        self._session.commit()

    # --- per-round enrichment (best-effort; reuses existing services unchanged) -------------

    def _enrich(
        self, target_ids: Sequence[int], profile_id: int, campaign: Campaign, actor: str
    ) -> None:
        eligible: list[int] = []
        for target_id in target_ids:
            try:
                target = self._qualification.target(target_id)
                if target.opportunity is not None and target.opportunity.description_text:
                    report = self._requirements.extract(target_id, actor=actor)
                    if report.outcome == "extracted":
                        campaign.requirements_extracted += 1
                    self._qualification.qualify(target_id, profile_id)  # re-qualify: never stale
                current = self._qualification.current(target_id, profile_id)
                if current.qualification.status is not QualificationStatus.EXCLUDED:
                    eligible.append(target_id)
            except DomainError:
                continue  # best-effort: skip this one target, never abort the round

        if eligible:
            contact_report = self._contacts.run(
                target_ids=eligible,
                role_categories=DEFAULT_ROLE_CATEGORIES,
                max_research_calls=min(len(eligible), MAX_CONTACT_CALLS_PER_ROUND),
                actor=actor,
            )
            campaign.contacts_proposed += contact_report.observations_created

        if self._llm is not None:
            for target_id in eligible:
                try:
                    package = self._packages.prepare(
                        target_id, ApplicationPrepareRequest(), actor=actor
                    )
                except DomainError:
                    continue
                campaign.packages_prepared += 1
                if package.draft_id is not None:
                    campaign.drafts_generated += 1

    # --- read -------------------------------------------------------------------------------

    def get(self, campaign_id: int) -> Campaign:
        campaign = repo.get_campaign(self._session, self._candidate_id(), campaign_id)
        if campaign is None:
            raise NotFoundError(f"Campaign {campaign_id} not found")
        return campaign

    def list(self, *, limit: int = MAX_PAGE, offset: int = 0) -> Sequence[Campaign]:
        return repo.list_campaigns(
            self._session, self._candidate_id(), limit=min(limit, MAX_PAGE), offset=offset
        )

    def funnel(self, campaign_id: int) -> CampaignFunnel:
        campaign = self.get(campaign_id)
        target_ids = repo.target_ids_for_campaign(self._session, campaign_id)

        qualified = uncertain = excluded = 0
        company_ids: set[int] = set()
        for target_id in target_ids:
            try:
                target = self._qualification.target(target_id)
                current = self._qualification.current(target_id, campaign.profile_id)
            except NotFoundError:
                continue
            company_ids.add(target.company_id)
            status = current.qualification.status
            if status is QualificationStatus.CANDIDATE:
                qualified += 1
            elif status is QualificationStatus.NEEDS_INFORMATION:
                uncertain += 1
            else:
                excluded += 1

        # A `Contact` row only exists once a human has ACCEPTED it (see `app.models.contacts`):
        # its mere presence, with an email channel, is already the human-confirmed fact.
        contacts_accepted_with_email = 0
        if company_ids:
            contacts_accepted_with_email = (
                self._session.scalar(
                    select(func.count(Contact.id.distinct()))
                    .join(ContactChannel, ContactChannel.contact_id == Contact.id)
                    .where(
                        Contact.company_id.in_(company_ids),
                        ContactChannel.kind == ChannelKind.EMAIL,
                    )
                )
                or 0
            )

        packages_by_status: dict[ApplicationPackageStatus, int] = dict.fromkeys(
            ApplicationPackageStatus, 0
        )
        packages_sent = 0
        if target_ids:
            for package in event_repo.list_packages_by_event(self._session, campaign.candidate_id):
                if package.target_id not in target_ids:
                    continue
                packages_by_status[package.status] += 1
            sent_packages = event_repo.list_packages_by_event(
                self._session,
                campaign.candidate_id,
                event_type=ApplicationEventType.SENT,
            )
            packages_sent = sum(1 for p in sent_packages if p.target_id in target_ids)

        raw_results_total = (
            self._session.scalar(
                select(func.sum(SearchRun.results_raw)).where(SearchRun.campaign_id == campaign_id)
            )
            or 0
        )

        return CampaignFunnel(
            campaign=CampaignRead.model_validate(campaign),
            raw_results_total=raw_results_total,
            targets_total=len(target_ids),
            qualified=qualified,
            uncertain=uncertain,
            excluded=excluded,
            contacts_proposed=campaign.contacts_proposed,
            contacts_accepted_with_email=contacts_accepted_with_email,
            packages_by_status=packages_by_status,
            packages_sent=packages_sent,
        )
