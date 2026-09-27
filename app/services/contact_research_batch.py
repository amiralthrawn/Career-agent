"""Contact research batch (step 8, Part 6): search many companies without re-searching one
company for a role category it already has a known contact for, and without ever exceeding an
explicit budget of provider calls.

Mirrors `app.services.research_batch.ResearchBatchService`'s shape exactly (deterministic,
grouped, budget-limited, partial fault isolation, one audit event, no score, no Celery/Redis) -
generalised from "per company" to "per (company, role category)", since several targets sharing a
company still need the search only ONCE per category: this module fetches the provider result a
single time and fans it out to every target of that company waiting on that category (see
`app.services.contact_research.store_observations`, called once per affected target so each one
keeps its own, never-mixed, `ContactResearchObservation` rows).
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
from app.models.enums import RoleCategory
from app.repositories import candidate_brain as brain_repo
from app.repositories import qualification as qualification_repo
from app.repositories.targets import get_company, list_contacts
from app.services.audit import AuditLog
from app.services.contact_research import build_query, store_observations

DEFAULT_MAX_RESEARCH_CALLS = 0  # capabilities default off, same convention as step 7
DEFAULT_ROLE_CATEGORIES = (
    RoleCategory.RECRUITER,
    RoleCategory.HR,
    RoleCategory.MANAGER,
    RoleCategory.TECH,
)


class ContactSearchOutcome(StrEnum):
    NOT_NEEDED = "not_needed"  # no target waiting on this (company, category)
    ALREADY_KNOWN = "already_known"  # a contact of this category already exists for the company
    RESEARCHED = "researched"  # the provider was called and its result stored
    RESEARCH_FAILED = "research_failed"  # the provider was called and failed
    SKIPPED_BUDGET = "skipped_budget"  # needed, but no provider or no budget left


@dataclass(frozen=True)
class ContactBatchItem:
    company_id: int
    role_category: RoleCategory
    targets_affected: tuple[int, ...]
    outcome: ContactSearchOutcome
    observations_created: int


@dataclass(frozen=True)
class ContactBatchReport:
    targets_processed: int
    searches_needed: int
    searches_researched: int
    searches_failed: int
    searches_skipped_budget: int
    searches_skipped_already_known: int
    observations_created: int
    items: tuple[ContactBatchItem, ...]


class ContactResearchBatchService:
    def __init__(self, session: Session, provider: ResearchProvider | None) -> None:
        self._session = session
        self._provider = provider

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    def run(
        self,
        *,
        target_ids: Sequence[int] | None = None,
        max_targets: int | None = None,
        role_categories: Sequence[RoleCategory] = DEFAULT_ROLE_CATEGORIES,
        max_research_calls: int = DEFAULT_MAX_RESEARCH_CALLS,
        actor: str = "api",
    ) -> ContactBatchReport:
        targets = self._select_targets(target_ids, max_targets)

        # 1. Group targets by company; every (company, role_category) is at most one search.
        targets_by_company: dict[int, list[Target]] = defaultdict(list)
        for target in targets:
            targets_by_company[target.company_id].append(target)

        # 2. Decide, per (company, category), whether a search is needed at all - never one when
        # a real, accepted `Contact` of that category is already known for the company. A direct
        # query (never the possibly-stale `Company.contacts` relationship already cached in this
        # session's identity map) so a contact accepted moments ago is never missed.
        known_by_company: dict[int, set[RoleCategory]] = {
            company_id: {c.role_category for c in list_contacts(self._session, company_id)}
            for company_id in targets_by_company
        }
        needed: dict[tuple[int, RoleCategory], list[int]] = {}
        for company_id, company_targets in targets_by_company.items():
            for role_category in role_categories:
                if role_category in known_by_company[company_id]:
                    continue  # ALREADY_KNOWN, recorded below with no provider call at all
                needed[(company_id, role_category)] = [t.id for t in company_targets]

        # Deterministic priority: the (company, category) affecting the most targets first.
        keys = sorted(needed, key=lambda k: (-len(needed[k]), k[0], k[1].value))

        # 3. Budget-limited, partially-tolerant research; one call per (company, category), fanned
        # out to every target waiting on it.
        items: list[ContactBatchItem] = []
        budget = max_research_calls
        for company_id, role_category in keys:
            target_ids_affected = tuple(needed[(company_id, role_category)])
            if self._provider is None or budget <= 0:
                items.append(
                    ContactBatchItem(
                        company_id,
                        role_category,
                        target_ids_affected,
                        ContactSearchOutcome.SKIPPED_BUDGET,
                        0,
                    )
                )
                continue
            budget -= 1
            company = get_company(self._session, company_id)
            assert company is not None
            try:
                result = self._provider.research(build_query(company, role_category))
            except ResearchError:
                items.append(
                    ContactBatchItem(
                        company_id,
                        role_category,
                        target_ids_affected,
                        ContactSearchOutcome.RESEARCH_FAILED,
                        0,
                    )
                )
                continue
            created = 0
            for target_id in target_ids_affected:
                created += len(
                    store_observations(
                        self._session,
                        target_id=target_id,
                        company_id=company_id,
                        role_category=role_category,
                        result=result,
                    )
                )
            self._session.commit()
            items.append(
                ContactBatchItem(
                    company_id,
                    role_category,
                    target_ids_affected,
                    ContactSearchOutcome.RESEARCHED,
                    created,
                )
            )

        # "Already known" categories are reported explicitly (never silently unseen, unlike a
        # category with no target waiting on it at all, which gets no item either way).
        for company_id, company_targets in targets_by_company.items():
            for role_category in role_categories:
                if role_category in known_by_company[company_id]:
                    items.append(
                        ContactBatchItem(
                            company_id,
                            role_category,
                            tuple(t.id for t in company_targets),
                            ContactSearchOutcome.ALREADY_KNOWN,
                            0,
                        )
                    )

        outcomes = [item.outcome for item in items]
        report = ContactBatchReport(
            targets_processed=len(targets),
            searches_needed=len(needed),
            searches_researched=outcomes.count(ContactSearchOutcome.RESEARCHED),
            searches_failed=outcomes.count(ContactSearchOutcome.RESEARCH_FAILED),
            searches_skipped_budget=outcomes.count(ContactSearchOutcome.SKIPPED_BUDGET),
            searches_skipped_already_known=outcomes.count(ContactSearchOutcome.ALREADY_KNOWN),
            observations_created=sum(item.observations_created for item in items),
            items=tuple(items),
        )
        AuditLog(self._session).record(
            AuditEventType.CONTACT_RESEARCH_BATCH_RUN,
            actor=actor,
            details={
                "rows": report.targets_processed,
                "created": report.searches_researched,
                "matched": report.searches_skipped_already_known,
                "rejected": report.searches_failed,
            },
        )
        self._session.commit()
        return report

    def _select_targets(
        self, target_ids: Sequence[int] | None, max_targets: int | None
    ) -> list[Target]:
        if target_ids is not None:
            candidate_id = self._candidate_id()
            targets = []
            for target_id in target_ids:
                target = self._session.get(Target, target_id)
                if target is None or target.candidate_id != candidate_id:
                    raise NotFoundError(f"Target {target_id} not found")
                targets.append(target)
        else:
            targets = list(
                qualification_repo.targets_to_qualify(self._session, self._candidate_id())
            )
        return targets[:max_targets] if max_targets is not None else targets
