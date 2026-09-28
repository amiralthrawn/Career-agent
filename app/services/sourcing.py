"""SourcingService (step 3c): from a provider's findings to qualified targets, with an audit trail.

Order of operations, each step before the next:

1. validate the request, load the SearchProfile, build the structured query and resolve the
   provider. NOTHING is called before all of this succeeds;
2. record a `running` SearchRun (committed: a crash leaves a trace);
3. call the provider (an explicit failure is a `failed` run, never an empty successful search);
4. convert raw results to `SourcedItem`s (`HitExtractor`, deterministic) and validate provenance;
5. ingest each item through the EXISTING `TargetService.create_target` (same de-duplication, same
   provenance rules as the API and the CSV import) and qualify the target through the EXISTING
   `QualificationService` (3a criteria, 3b requirement matches). No second engine;
6. record one `SearchRunItem` per result and finish the run (counters, status, audit event).

Each item is atomic (a savepoint): an invalid or failing item never cancels the items already
ingested. The run is committed once, together with its audit event. If something unexpected
happens, everything ingested by that run is rolled back and the run is marked `failed`; the
exception is re-raised, never swallowed.

Spontaneous sourcing (`companies` mode) creates a Company and a spontaneous Target
(`opportunity_id IS NULL`), never an Opportunity, and never says the company is hiring. Offers
found in `offers` mode create Company + Opportunity + Target. The Target de-duplication rules of
step 2 are the reference: a spontaneous target and an offer target of one company stay distinct.

`all` mode runs BOTH flows from ONE provider call, in ONE `SearchRun`: `_extract_hit` first tries
`HitExtractor` in `offers` mode; a hit that states a company but no offer (`missing_offer_title`)
is retried in `companies` mode instead of being wasted as a rejection. Nothing else changes -
`_process_item` reads the resulting `SourcedItem.opportunity` (already `None` or not, exactly per
`HitExtractor`'s own rule) rather than the run's mode, so it treats an `all`-mode item exactly
like a pure-mode one. No second engine, no extra provider call, no change to qualification or 3b.

No score, no ranking, no e-mail, no sending.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError, NotFoundError, UnprocessableError
from app.core.normalize import normalize_url
from app.core.secrets import PERPLEXITY_API_KEY, SecretStore, SecretStoreError
from app.integrations.sourcing.perplexity import PerplexityWebSearchProvider
from app.integrations.sourcing.ports import (
    OfferFetchResult,
    ProviderError,
    ProviderErrorCode,
    QueryCriterion,
    SearchHit,
    SearchQuery,
    SourcedItem,
    SourcingProviders,
    WebSearchProvider,
    WebSearchResult,
)
from app.models import SearchProfile, SearchRun, SearchRunItem
from app.models.audit import AuditEventType
from app.models.enums import (
    CriterionDimension,
    CriterionOperator,
    EmploymentType,
    ItemReason,
    ProviderKind,
    RunErrorCode,
    SearchRunItemOutcome,
    SearchRunStatus,
    SourceKind,
    SourcingMode,
)
from app.repositories import candidate_brain as brain_repo
from app.repositories import sourcing as repo
from app.repositories.targets import stage
from app.schemas.sourcing import SearchRunCreate
from app.schemas.targets import TargetCreate
from app.services.audit import AuditLog
from app.services.hit_extraction import MAX_EXCERPT_CHARS, Extraction, HitExtractor, Rejection
from app.services.offers_research import record_offers_research
from app.services.qualification import QualificationService
from app.services.search_profiles import SearchProfileService
from app.services.sources import validate_spec
from app.services.targets import TargetService

MAX_PAGE = 100
MAX_SOURCES_RECORDED = 20
MAX_SOURCE_LABEL_CHARS = 120
ALLOWED_SOURCE_KINDS = (SourceKind.OFFICIAL_API, SourceKind.PUBLIC_PAGE)


def default_web_search_provider(
    settings: Settings, secrets: SecretStore
) -> WebSearchProvider | None:
    """`None` by default: a run then has no `web` provider to select (`422`, unknown provider)
    before anything is called. The SAME capability gate as company/contact research
    (`RESEARCH_ENABLED`, `PERPLEXITY_PRESET`, the `perplexity_api_key` secret) - it is the same
    Perplexity account used for a different task (see `app.integrations.sourcing.perplexity`),
    never a second secret or config flag to manage.
    """
    if not settings.research_enabled or not settings.perplexity_preset:
        return None
    try:
        if not secrets.exists(PERPLEXITY_API_KEY):
            return None
    except SecretStoreError:
        return None  # fail closed: no secure backend, no provider
    return PerplexityWebSearchProvider(secrets, settings.perplexity_preset)


@dataclass
class _Tally:
    results_raw: int = 0
    targets_created: int = 0
    targets_existing: int = 0
    companies_created: int = 0
    opportunities_created: int = 0
    qualifications_created: int = 0
    items_rejected: int = 0
    item_errors: int = 0


@dataclass
class _Fetched:
    completed: bool
    extractions: list[Extraction]
    raw_count: int
    exceeded: bool
    sources_consulted: list[str]


class SourcingService:
    def __init__(
        self,
        session: Session,
        providers: SourcingProviders,
        extractor: HitExtractor | None = None,
    ) -> None:
        self._session = session
        self._providers = providers
        self._extractor = extractor or HitExtractor()
        self._profiles = SearchProfileService(session)

    # --- run --------------------------------------------------------------------------

    def run(self, data: SearchRunCreate, *, actor: str = "api") -> SearchRun:
        candidate_id = self._candidate_id()
        profile = (
            self._profiles.get_profile(data.profile_id)
            if data.profile_id is not None
            else self._profiles.active_profile()
        )
        query = self._build_query(profile, data)
        kind = self._resolve_kind(data)  # every check above is done before any provider call

        run = stage(
            self._session,
            SearchRun(
                candidate_id=candidate_id,
                profile_id=profile.id,
                mode=data.mode,
                provider=data.provider,
                provider_kind=kind,
                status=SearchRunStatus.RUNNING,
                query=_query_metadata(query),
                max_results=data.max_results,
            ),
        )
        self._session.commit()  # the run is on record before the provider is called
        run_id = run.id
        try:
            return self._execute(run, profile, query, kind, data, actor)
        except Exception:
            self._session.rollback()
            self._mark_unexpected_failure(run_id)
            raise

    def _execute(
        self,
        run: SearchRun,
        profile: SearchProfile,
        query: SearchQuery,
        kind: ProviderKind,
        data: SearchRunCreate,
        actor: str,
    ) -> SearchRun:
        try:
            fetched = self._fetch(kind, data, query)
        except ProviderError as error:
            return self._finish(
                run,
                _Tally(),
                {},
                SearchRunStatus.FAILED,
                [{"code": RunErrorCode.PROVIDER_ERROR.value, "detail": error.code.value}],
                [],
                actor,
            )

        tally = _Tally(results_raw=fetched.raw_count)
        breakdown: dict[str, _Tally] = {}
        position = 0
        for extraction in fetched.extractions:
            for rejection in extraction.rejections:
                self._record_rejection(run, position, rejection, tally)
                position += 1
            for item in extraction.items:
                self._process_item(run, position, item, profile, query, tally, breakdown)
                position += 1

        errors: list[dict[str, str]] = []
        if not fetched.completed:
            errors.append({"code": RunErrorCode.PROVIDER_INCOMPLETE.value})
        if fetched.exceeded:
            errors.append({"code": RunErrorCode.PROVIDER_EXCEEDED_LIMIT.value})
        if tally.item_errors:
            errors.append({"code": RunErrorCode.ITEM_ERRORS.value})
        if not fetched.completed and fetched.raw_count == 0:
            status = SearchRunStatus.FAILED  # incomplete AND empty: nothing can be concluded
        else:
            status = SearchRunStatus.COMPLETED_WITH_ERRORS if errors else SearchRunStatus.COMPLETED
        return self._finish(run, tally, breakdown, status, errors, fetched.sources_consulted, actor)

    # --- provider call ----------------------------------------------------------------

    def _fetch(self, kind: ProviderKind, data: SearchRunCreate, query: SearchQuery) -> _Fetched:
        if kind is ProviderKind.WEB_SEARCH:
            web_result = self._providers.web[data.provider].search(query)
            if not isinstance(web_result, WebSearchResult):
                raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)
            hits = list(web_result.hits)
            if not all(isinstance(hit, SearchHit) for hit in hits):
                raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)
            kept = hits[: data.max_results]
            return _Fetched(
                web_result.completed,
                [self._extract_hit(hit, data) for hit in kept],
                len(hits),
                len(hits) > data.max_results,
                _labels(web_result.sources_consulted),
            )
        offer_result = self._providers.offers[data.provider].fetch(query)
        if not isinstance(offer_result, OfferFetchResult):
            raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)
        items = list(offer_result.items)
        if not all(isinstance(item, SourcedItem) for item in items):
            raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)
        return _Fetched(
            offer_result.completed,
            [Extraction(items=(item,)) for item in items[: data.max_results]],
            len(items),
            len(items) > data.max_results,
            _labels(offer_result.sources_consulted),
        )

    def _extract_hit(self, hit: SearchHit, data: SearchRunCreate) -> Extraction:
        if hit.provider != data.provider:  # a hit must come from the provider that was asked
            return Extraction(
                rejections=(
                    Rejection(ItemReason.INVALID_PROVENANCE, source_url=_public_url(hit.url)),
                )
            )
        if data.mode is not SourcingMode.ALL:
            return self._extractor.extract(hit, data.mode)
        # `all`: try `offers` first; a hit that states a company but no offer is retried as a
        # spontaneous lead instead of being wasted as a `missing_offer_title` rejection. A hit
        # that identifies nothing at all fails the same way either way, so it is not retried.
        offer_attempt = self._extractor.extract(hit, SourcingMode.OFFERS)
        if offer_attempt.items:
            return offer_attempt
        if offer_attempt.rejections[0].reason is ItemReason.MISSING_OFFER_TITLE:
            return self._extractor.extract(hit, SourcingMode.COMPANIES)
        return offer_attempt

    # --- items ------------------------------------------------------------------------

    def _process_item(
        self,
        run: SearchRun,
        position: int,
        item: SourcedItem,
        profile: SearchProfile,
        query: SearchQuery,
        tally: _Tally,
        breakdown: dict[str, _Tally],
    ) -> None:
        rejection = _validate_item(item, query.mode)
        if rejection is not None:
            self._record_rejection(run, position, rejection, tally, excerpt=item.excerpt)
            return
        # `item.opportunity` is already `None` or not, exactly per `HitExtractor`'s own rule
        # (never set in `companies` mode, always set on a successful `offers` extraction) - this
        # holds for a pure-mode run and for an `all`-mode one alike, so the run's own mode never
        # needs to be re-checked here.
        is_offer = item.opportunity is not None
        request = TargetCreate(
            company=item.company,
            opportunity=item.opportunity,
            # A spontaneous target carries the contract the profile asks for (when it asks for
            # exactly one); an offer target takes the offer's own contract, never the profile's.
            contract_type=query.contract_type if not is_offer else None,
        )
        try:
            with self._session.begin_nested():  # each item is atomic
                outcome = TargetService(self._session).create_target(
                    request, source=item.source, commit=False
                )
                if outcome.opportunity is not None:
                    record_offers_research(outcome.company.entity, offers_found=1, completed=False)
                qualification = QualificationService(self._session).qualify(
                    outcome.target.id, profile.id, commit=False
                )
        except DomainError:
            self._record_error(run, position, ItemReason.INGESTION_REFUSED, tally, item)
            return
        except SQLAlchemyError:
            self._record_error(run, position, ItemReason.DATABASE_ERROR, tally, item)
            return

        flow = breakdown.setdefault(
            SourcingMode.OFFERS.value if is_offer else SourcingMode.COMPANIES.value, _Tally()
        )
        for bucket in (tally, flow):
            bucket.targets_created += 1 if outcome.created else 0
            bucket.targets_existing += 0 if outcome.created else 1
            bucket.companies_created += 1 if outcome.company.created else 0
            bucket.opportunities_created += (
                1 if outcome.opportunity and outcome.opportunity.created else 0
            )
            bucket.qualifications_created += 1 if qualification.created else 0
        stage(
            self._session,
            SearchRunItem(
                run_id=run.id,
                position=position,
                outcome=(
                    SearchRunItemOutcome.TARGET_CREATED
                    if outcome.created
                    else SearchRunItemOutcome.TARGET_EXISTING
                ),
                fields=[],
                target_id=outcome.target.id,
                qualification_id=qualification.qualification.id,
                excerpt=_excerpt(item.excerpt),
            ),
        )

    def _record_rejection(
        self,
        run: SearchRun,
        position: int,
        rejection: Rejection,
        tally: _Tally,
        *,
        excerpt: str | None = None,
    ) -> None:
        tally.items_rejected += 1
        stage(
            self._session,
            SearchRunItem(
                run_id=run.id,
                position=position,
                outcome=SearchRunItemOutcome.REJECTED,
                reason=rejection.reason,
                fields=list(rejection.fields),
                source_url=rejection.source_url,
                excerpt=_excerpt(excerpt),
            ),
        )

    def _record_error(
        self, run: SearchRun, position: int, reason: ItemReason, tally: _Tally, item: SourcedItem
    ) -> None:
        tally.item_errors += 1
        stage(
            self._session,
            SearchRunItem(
                run_id=run.id,
                position=position,
                outcome=SearchRunItemOutcome.ERROR,
                reason=reason,
                fields=[],
                source_url=_public_url(item.source.url),
                excerpt=_excerpt(item.excerpt),
            ),
        )

    # --- finishing --------------------------------------------------------------------

    def _finish(
        self,
        run: SearchRun,
        tally: _Tally,
        breakdown: dict[str, _Tally],
        status: SearchRunStatus,
        errors: list[dict[str, str]],
        sources: list[str],
        actor: str,
    ) -> SearchRun:
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.errors = errors
        run.sources_consulted = sources
        for name, value in vars(tally).items():
            setattr(run, name, value)
        if run.mode is SourcingMode.ALL:
            # Only the counters actually tracked per flow (see `_process_item`): `results_raw`/
            # `items_rejected`/`item_errors` are never split by flow, so they are left out here
            # rather than shown as a misleading, always-zero per-flow figure.
            run.breakdown = {
                flow: {
                    name: value
                    for name, value in vars(flow_tally).items()
                    if name not in ("results_raw", "items_rejected", "item_errors")
                }
                for flow, flow_tally in breakdown.items()
            }
        # The audit event commits together with the run and everything it ingested.
        AuditLog(self._session).record(
            AuditEventType.SOURCING_RUN,
            actor=actor,
            subject=f"search_run:{run.id}",
            details={
                "mode": run.mode.value,
                "rows": tally.results_raw,
                "created": tally.targets_created,
                "matched": tally.targets_existing,
                "rejected": tally.items_rejected,
            },
        )
        self._session.refresh(run)
        return run

    def _mark_unexpected_failure(self, run_id: int) -> None:
        """After an unexpected exception: everything ingested is gone, the run says so."""
        run = self._session.get(SearchRun, run_id)
        if run is None:
            return
        run.status = SearchRunStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.errors = [{"code": RunErrorCode.UNEXPECTED_ERROR.value}]
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()

    # --- validation before any provider call ------------------------------------------

    def _build_query(self, profile: SearchProfile, data: SearchRunCreate) -> SearchQuery:
        criteria = tuple(
            QueryCriterion(c.dimension, c.operator, tuple(c.match_values), c.level)
            for c in profile.criteria
            if c.active and c.dimension is not CriterionDimension.CONSTRAINT
        )
        if not criteria:
            raise UnprocessableError("The search profile has no active, searchable criterion")
        contracts = {
            value
            for c in criteria
            if c.dimension is CriterionDimension.CONTRACT_TYPE
            and c.operator is CriterionOperator.ANY_OF
            for value in c.values
        }
        contract = EmploymentType(next(iter(contracts))) if len(contracts) == 1 else None
        return SearchQuery(
            mode=data.mode,
            profile_id=profile.id,
            criteria=criteria,
            max_results=data.max_results,
            contract_type=contract,
        )

    def _resolve_kind(self, data: SearchRunCreate) -> ProviderKind:
        if data.provider in self._providers.web:
            return ProviderKind.WEB_SEARCH
        if data.provider in self._providers.offers:
            if data.mode is not SourcingMode.OFFERS:
                raise UnprocessableError("An offer source can only be used in offers mode")
            return ProviderKind.OFFER_SOURCE
        raise UnprocessableError("Unknown sourcing provider")

    # --- reads ------------------------------------------------------------------------

    def get(self, run_id: int) -> SearchRun:
        run = repo.get_run(self._session, self._candidate_id(), run_id)
        if run is None:
            raise NotFoundError(f"Search run {run_id} not found")
        return run

    def items(self, run_id: int) -> list[SearchRunItem]:
        self.get(run_id)
        return list(repo.list_items(self._session, run_id))

    def list(self, *, profile_id: int | None, limit: int, offset: int) -> list[SearchRun]:
        return list(
            repo.list_runs(
                self._session,
                self._candidate_id(),
                profile_id=profile_id,
                limit=min(limit, MAX_PAGE),
                offset=offset,
            )
        )

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id


def _validate_item(item: SourcedItem, mode: SourcingMode) -> Rejection | None:
    """Provenance and completeness checks that do not depend on how the item was produced."""
    source = item.source
    if source.kind not in ALLOWED_SOURCE_KINDS or not source.label.strip():
        return Rejection(ItemReason.INVALID_PROVENANCE)
    try:
        validate_spec(source)
    except UnprocessableError:
        return Rejection(ItemReason.INVALID_PROVENANCE)
    if source.url is not None and normalize_url(source.url) is None:
        return Rejection(ItemReason.INVALID_PROVENANCE)
    if mode is SourcingMode.OFFERS and item.opportunity is None:
        return Rejection(ItemReason.OFFER_REQUIRED, source_url=_public_url(source.url))
    return None


def _public_url(url: str | None) -> str | None:
    return normalize_url(url) if url else None


def _excerpt(text: str | None) -> str | None:
    return text[:MAX_EXCERPT_CHARS] if text else None


def _labels(sources: tuple[str, ...]) -> list[str]:
    return [label[:MAX_SOURCE_LABEL_CHARS] for label in sources[:MAX_SOURCES_RECORDED]]


def _query_metadata(query: SearchQuery) -> dict[str, object]:
    """The structured query as stored: the profile's search terms, nothing about the candidate."""
    return {
        "mode": query.mode.value,
        "contract_type": query.contract_type.value if query.contract_type else None,
        "criteria": [
            {
                "dimension": c.dimension.value,
                "operator": c.operator.value,
                "level": c.level.value,
                "values": list(c.values)[:20],
            }
            for c in query.criteria
        ],
    }
