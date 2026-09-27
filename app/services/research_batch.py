"""Batch: qualify many targets without researching all of them (step 7).

```
SOURCING -> opportunities -> QUALIFICATION (deterministic, unchanged)
                                   |
                 +-----------------+-----------------+
                 |                 |                 |
             candidate    needs_information       excluded
                 |                 |                 |
                 |          RESEARCH PLAN             |
                 |          (grouped by company,      |
                 |           budget-limited)           |
                 |                 |                 |
                 |            Perplexity              |
                 |                 |                 |
                 |          accepted observations      |
                 |                 |                 |
                 |          REQUALIFICATION            |
                 |          (the SAME qualify() call, |
                 |           now with more free text)  |
                 +-----------------+-----------------+
                                   |
                          final qualification (per target)
```

For 500 opportunities this does at most a few dozen research calls, not 500: research is planned
per COMPANY (many opportunities share one company) and only for `BLOCKING` needs (a `REQUIRED`
criterion currently `unknown` - the only kind that could still change a status), bounded by
`max_research_calls`. `unknown ≠ false`, immutability, the fingerprint and the existing
`decide_status` rules are all untouched: this module only decides WHETHER and WHAT to ask
Perplexity, and re-runs the SAME, unchanged `QualificationService.qualify` afterwards.

A provider failure on one company is isolated (`RESEARCH_FAILED`, recorded explicitly - never
silently `unknown -> false`) and never loses the other companies' results.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.integrations.research.ports import ResearchError, ResearchProvider
from app.models import Target
from app.models.audit import AuditEventType
from app.models.enums import QualificationStatus
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as qualification_repo
from app.repositories.targets import get_company
from app.services.audit import AuditLog
from app.services.qualification import QualificationService
from app.services.research_planning import (
    InformationNeed,
    NeedPriority,
    ResearchPlan,
    accept_observations,
    information_needs,
    plan_for_company,
)
from app.services.search_profiles import SearchProfileService

DEFAULT_MAX_RESEARCH_CALLS = 0  # capabilities default off, like `LLM_ENABLED`/`RESEARCH_ENABLED`


class ResearchOutcome(StrEnum):
    NOT_NEEDED = "not_needed"  # no blocking information need for this target
    RESEARCHED = "researched"  # its company's plan was researched successfully
    RESEARCH_FAILED = "research_failed"  # its company's plan was attempted and failed
    SKIPPED_BUDGET = "skipped_budget"  # a plan existed but no provider or no budget was left


@dataclass(frozen=True)
class BatchItem:
    target_id: int
    company_id: int
    initial_status: QualificationStatus
    research_outcome: ResearchOutcome
    final_status: QualificationStatus
    requalified: bool
    needs: tuple[InformationNeed, ...]


@dataclass(frozen=True)
class BatchReport:
    profile_id: int
    targets_processed: int
    plans_built: int
    plans_researched: int
    plans_failed: int
    plans_skipped_budget: int
    requalified: int
    items: tuple[BatchItem, ...]


class ResearchBatchService:
    def __init__(self, session: Session, provider: ResearchProvider | None) -> None:
        self._session = session
        self._provider = provider
        self._qualifications = QualificationService(session)

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    def run(
        self,
        profile_id: int | None = None,
        *,
        target_ids: Sequence[int] | None = None,
        max_targets: int | None = None,
        max_research_calls: int = DEFAULT_MAX_RESEARCH_CALLS,
        actor: str = "api",
    ) -> BatchReport:
        profiles = SearchProfileService(self._session)
        profile = (
            profiles.get_profile(profile_id)
            if profile_id is not None
            else (profiles.active_profile())
        )
        targets = self._select_targets(target_ids, max_targets)

        # 1. Initial qualification (deterministic, unchanged) + information needs, per target.
        initial: dict[int, QualificationStatus] = {}
        needs_by_target: dict[int, tuple[InformationNeed, ...]] = {}
        needs_by_company: dict[int, list[InformationNeed]] = defaultdict(list)
        for target in targets:
            outcome = self._qualifications.qualify(target.id, profile.id)
            initial[target.id] = outcome.qualification.status
            needs: tuple[InformationNeed, ...] = ()
            if outcome.qualification.status is QualificationStatus.NEEDS_INFORMATION:
                needs = tuple(information_needs(target, outcome.qualification))
            needs_by_target[target.id] = needs
            blocking = [n for n in needs if n.priority is NeedPriority.BLOCKING]
            if blocking:
                needs_by_company[target.company_id].extend(blocking)

        # 2. One research plan per company (never per target): this is what turns many
        # opportunities into few searches.
        plans: list[ResearchPlan] = []
        for company_id, company_needs in needs_by_company.items():
            company = get_company(self._session, company_id)
            assert company is not None  # it owns targets just qualified above
            plan = plan_for_company(company, company_needs)
            if plan is not None:
                plans.append(plan)
        # Deterministic priority: the company affecting the most targets first, tie-broken by id.
        plans.sort(key=lambda p: (-len({n.target_id for n in p.needs}), p.company_id))

        # 3. Budget-limited, partially-tolerant research.
        outcome_by_company: dict[int, ResearchOutcome] = {}
        budget = max_research_calls
        for plan in plans:
            if self._provider is None or budget <= 0:
                outcome_by_company[plan.company_id] = ResearchOutcome.SKIPPED_BUDGET
                continue
            budget -= 1
            try:
                result = self._provider.research(plan.query)
            except ResearchError:
                outcome_by_company[plan.company_id] = ResearchOutcome.RESEARCH_FAILED
                continue
            accept_observations(self._session, plan.company_id, result)
            self._session.commit()
            outcome_by_company[plan.company_id] = ResearchOutcome.RESEARCHED

        # `expire_on_commit=False` is the convention throughout this project (fewer queries), so
        # a `Company` already read above (e.g. while building the first qualification) keeps a
        # STALE, empty `research_facts` unless explicitly expired here. Without this,
        # requalification below would silently see no accepted facts at all.
        #
        # Only the specific companies that actually gained a fact are expired - and only their
        # `research_facts` collection, not the whole object or the whole session - so a target
        # untouched by this run (the common case in a large batch) keeps its already-loaded data
        # and is not forced through an extra round-trip it does not need.
        for company_id, research_outcome in outcome_by_company.items():
            if research_outcome is ResearchOutcome.RESEARCHED:
                company = get_company(self._session, company_id)
                if company is not None:
                    self._session.expire(company, ["research_facts"])

        # 4. Requalify only what accepted new facts; everything else keeps its initial result.
        items: list[BatchItem] = []
        requalified = 0
        for target in targets:
            needs = needs_by_target[target.id]
            blocking = [n for n in needs if n.priority is NeedPriority.BLOCKING]
            research_outcome = (
                outcome_by_company[target.company_id] if blocking else ResearchOutcome.NOT_NEEDED
            )
            final_status = initial[target.id]
            did_requalify = False
            if research_outcome is ResearchOutcome.RESEARCHED:
                new_outcome = self._qualifications.qualify(target.id, profile.id)
                final_status = new_outcome.qualification.status
                did_requalify = True
                requalified += 1
            items.append(
                BatchItem(
                    target_id=target.id,
                    company_id=target.company_id,
                    initial_status=initial[target.id],
                    research_outcome=research_outcome,
                    final_status=final_status,
                    requalified=did_requalify,
                    needs=needs,
                )
            )

        outcomes = list(outcome_by_company.values())
        report = BatchReport(
            profile_id=profile.id,
            targets_processed=len(targets),
            plans_built=len(plans),
            plans_researched=outcomes.count(ResearchOutcome.RESEARCHED),
            plans_failed=outcomes.count(ResearchOutcome.RESEARCH_FAILED),
            plans_skipped_budget=outcomes.count(ResearchOutcome.SKIPPED_BUDGET),
            requalified=requalified,
            items=tuple(items),
        )
        AuditLog(self._session).record(
            AuditEventType.RESEARCH_BATCH_RUN,
            actor=actor,
            details={
                "rows": report.targets_processed,
                "created": report.plans_researched,
                "matched": report.plans_skipped_budget,
                "rejected": report.plans_failed,
            },
        )
        self._session.commit()
        return report

    def _select_targets(
        self, target_ids: Sequence[int] | None, max_targets: int | None
    ) -> list[Target]:
        if target_ids is not None:
            targets = [self._qualifications.target(target_id) for target_id in target_ids]
        else:
            targets = list(
                qualification_repo.targets_to_qualify(self._session, self._candidate_id())
            )
        return targets[:max_targets] if max_targets is not None else targets
