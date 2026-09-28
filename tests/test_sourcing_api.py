"""Sourcing end to end (step 3c) with FICTIONAL providers: no network, no real service.

The providers below are in-memory fakes. What is exercised is the domain: extraction, provenance,
ingestion through the existing services, de-duplication, qualification, run bookkeeping.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.integrations.sourcing.ports import ProviderErrorCode, SourcedItem
from app.models import (
    AuditEvent,
    Company,
    Opportunity,
    Qualification,
    SearchRun,
    Source,
    Target,
)
from app.models.audit import AuditEventType
from app.models.enums import OffersResearchStatus, SourceKind
from app.models.sources import SourceSpec
from app.services.targets import TargetService
from tests.qualification_factory import crit, make_profile, qualification
from tests.requirements_factory import add_skill, extract
from tests.sourcing_fakes import (
    BOARD,
    SENTINEL,
    WEB,
    FakeOfferSource,
    FakeWebProvider,
    company_hit,
    hit,
    install,
    item,
    run_of,
    start,
)


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture
def profile(client: TestClient) -> dict[str, Any]:
    return make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("role", ["analyst"], "preferred"),
        ],
    )


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def ok(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    response = start(client, **kwargs)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


# --- A. Offers --------------------------------------------------------------------------------


def test_an_offer_result_creates_company_offer_target_provenance_and_qualification(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit()]))

    run = ok(client)

    assert (run["status"], run["results_raw"]) == ("completed", 1)
    assert (run["targets_created"], run["companies_created"], run["opportunities_created"]) == (
        1,
        1,
        1,
    )
    assert run["qualifications_created"] == 1 and run["finished_at"]
    company = db_session.scalars(select(Company)).one()
    offer = db_session.scalars(select(Opportunity)).one()
    target = db_session.scalars(select(Target)).one()
    assert (company.name, offer.title, offer.company_id) == (
        "Fixture Corp",
        "Data Analyst Intern",
        company.id,
    )
    assert target.opportunity_id == offer.id and target.mode == "offer"
    # provenance: the page the result points to, and the provider that returned it
    for source in (company.source, offer.source, target.source):
        assert source.kind is SourceKind.PUBLIC_PAGE
        assert source.url == "https://search.example.invalid/offers/1"
        assert source.reference == WEB
    # the offer is NOT claimed to be open, and the snippet is not passed off as offer text
    assert offer.status.value == "unknown" and offer.description_text is None
    # the existing qualification service ran (3a)
    detail = run_of(client, run["id"])
    (row,) = detail["items"]
    assert row["outcome"] == "target_created" and row["target_id"] == target.id
    assert qualification(client, target.id)["id"] == row["qualification_id"]
    assert company.offers_research is OffersResearchStatus.FOUND  # an offer was recorded


def test_ingesting_the_same_results_again_is_idempotent(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit()]))
    ok(client)
    before = {
        model: count(db_session, model)
        for model in (Company, Opportunity, Target, Source, Qualification)
    }

    second = ok(client)

    assert (second["targets_created"], second["targets_existing"]) == (0, 1)
    assert (second["companies_created"], second["opportunities_created"]) == (0, 0)
    assert second["qualifications_created"] == 0  # same inputs: the qualification is reused
    assert second["status"] == "completed"
    assert {
        model: count(db_session, model)
        for model in (Company, Opportunity, Target, Source, Qualification)
    } == before
    assert run_of(client, second["id"])["items"][0]["outcome"] == "target_existing"


def test_two_offers_of_one_company_give_two_targets_and_one_company(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [
                hit("Data Analyst Intern at Fixture Corp", "https://search.example.invalid/o/1"),
                hit("BI Analyst Intern at Fixture Corp", "https://search.example.invalid/o/2"),
                hit("Data Analyst Intern at Fixture Corp", "https://search.example.invalid/o/1"),
            ]
        ),
    )

    run = ok(client)

    assert (run["targets_created"], run["targets_existing"], run["companies_created"]) == (2, 1, 1)
    assert (
        count(db_session, Company),
        count(db_session, Opportunity),
        count(db_session, Target),
    ) == (
        1,
        2,
        2,
    )
    targets = db_session.scalars(select(Target)).all()
    assert len({t.opportunity_id for t in targets}) == 2  # one target per offer


def test_an_offer_source_creates_the_same_model_with_its_own_provenance(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, offers=FakeOfferSource([item(description="Kubernetes is required.")]))

    run = ok(client, provider=BOARD)

    assert (run["provider_kind"], run["status"], run["targets_created"]) == (
        "offer_source",
        "completed",
        1,
    )
    offer = db_session.scalars(select(Opportunity)).one()
    assert offer.source.kind is SourceKind.OFFICIAL_API and offer.source.reference == "feed:1"
    assert offer.description_text == "Kubernetes is required."  # structured text, kept as given


@pytest.mark.parametrize(
    "source",
    [
        SourceSpec(SourceKind.MANUAL, "Typed by hand", reference="x"),
        SourceSpec(SourceKind.IMPORT_FILE, "A file", reference="row 1"),
        SourceSpec(SourceKind.OFFICIAL_API, "No locator at all"),
        SourceSpec(SourceKind.PUBLIC_PAGE, "Bad url", url="not a url"),
        SourceSpec(SourceKind.OFFICIAL_API, "   ", reference="feed:1"),
    ],
)
def test_an_item_with_missing_or_invalid_provenance_is_rejected(
    client: TestClient, db_session: Session, profile: dict[str, Any], source: SourceSpec
) -> None:
    install(client, offers=FakeOfferSource([item(source=source)]))

    run = ok(client, provider=BOARD)

    assert (run["status"], run["items_rejected"], run["targets_created"]) == ("completed", 1, 0)
    (row,) = run_of(client, run["id"])["items"]
    assert (row["outcome"], row["reason"]) == ("rejected", "invalid_provenance")
    assert count(db_session, Company) == count(db_session, Target) == 0


def test_a_hit_from_another_provider_than_the_one_asked_is_rejected(
    client: TestClient, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit(provider="someone-else")]))

    run = ok(client)

    assert run_of(client, run["id"])["items"][0]["reason"] == "invalid_provenance"


def test_an_offer_source_item_without_an_offer_is_rejected_in_offers_mode(
    client: TestClient, profile: dict[str, Any]
) -> None:
    install(client, offers=FakeOfferSource([item(title=None)]))

    run = ok(client, provider=BOARD)

    assert run_of(client, run["id"])["items"][0]["reason"] == "offer_required"


def test_an_insufficient_company_identity_is_rejected_with_a_reason(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [
                hit("Data Analyst Intern", "https://search.example.invalid/a"),
                hit("Analyst | Fixture Corp", "https://search.example.invalid/b"),
                hit("Analyst at Fixture Corp", ""),
            ]
        ),
    )

    run = ok(client)

    rows = run_of(client, run["id"])["items"]
    assert [r["reason"] for r in rows] == [
        "missing_company_name",
        "missing_company_name",
        "missing_source_url",
    ]
    assert (run["items_rejected"], run["targets_created"], run["results_raw"]) == (3, 0, 3)
    assert count(db_session, Company) == count(db_session, Target) == 0
    assert rows[0]["source_url"] == "https://search.example.invalid/a"  # public URL, for review


# --- B. Spontaneous ---------------------------------------------------------------------------


def test_a_company_result_creates_a_spontaneous_target_and_no_false_offer(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([company_hit()]))

    run = ok(client, mode="companies")

    assert (run["status"], run["targets_created"], run["opportunities_created"]) == (
        "completed",
        1,
        0,
    )
    target = db_session.scalars(select(Target)).one()
    assert target.opportunity_id is None and target.mode == "spontaneous"
    assert count(db_session, Opportunity) == 0
    assert target.contract_type is not None and target.contract_type.value == "apprenticeship"
    assert target.relevance_note is None  # nothing says the company recruits


def test_a_discovered_company_is_never_marked_as_recruiting(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [company_hit(offer_title="Data Analyst Intern", offer_contract="apprenticeship")]
        ),
    )

    ok(client, mode="companies")

    company = db_session.scalars(select(Company)).one()
    assert company.offers_research is OffersResearchStatus.NOT_STARTED
    assert company.offers_research_at is None
    assert count(db_session, Opportunity) == 0  # offer attributes are ignored in companies mode


def test_the_profile_contract_is_used_only_when_it_asks_for_exactly_one(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship", "internship"], "preferred")])
    provider = FakeWebProvider([company_hit()])
    install(client, web=provider)

    ok(client, mode="companies")

    assert provider.queries[0].contract_type is None
    assert db_session.scalars(select(Target)).one().contract_type is None


def test_criteria_that_need_an_offer_stay_unknown_for_a_spontaneous_target(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("role", ["analyst"], "required")])
    install(client, web=FakeWebProvider([company_hit()]))

    ok(client, mode="companies")

    target = db_session.scalars(select(Target)).one()
    data = qualification(client, target.id)
    (result,) = data["results"]
    assert (result["outcome"], result["code"]) == ("unknown", "no_offer")  # 3a rule, unchanged
    assert data["status"] == "needs_information"  # never excluded, never candidate by default


def test_a_company_can_have_a_spontaneous_target_and_an_offer_target_at_once(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    site = {"company_website": "https://fixture-corp.example.invalid"}
    install(client, web=FakeWebProvider([company_hit(**site)]))
    ok(client, mode="companies")
    install(client, web=FakeWebProvider([hit(**site)]))

    run = ok(client, mode="offers")

    assert (run["companies_created"], run["targets_created"]) == (0, 1)  # same company
    targets = db_session.scalars(select(Target).order_by(Target.id)).all()
    assert len({t.company_id for t in targets}) == 1
    assert [t.mode for t in targets] == ["spontaneous", "offer"]
    # and each stays unique: running both again creates nothing
    assert ok(client, mode="offers")["targets_created"] == 0
    install(client, web=FakeWebProvider([company_hit(**site)]))
    assert ok(client, mode="companies")["targets_created"] == 0
    assert count(db_session, Target) == 2


# --- C. SearchRun -----------------------------------------------------------------------------


def test_a_successful_run_records_counters_query_and_provider(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit(), company_hit()], sources=("fake-index",)))

    run = ok(client, max_results=10)

    assert (run["mode"], run["provider"], run["provider_kind"]) == ("offers", WEB, "web_search")
    assert run["profile_id"] == profile["id"] and run["max_results"] == 10
    assert run["sources_consulted"] == ["fake-index"] and run["errors"] == []
    assert run["query"]["criteria"][0] == {
        "dimension": "contract_type",
        "operator": "any_of",
        "level": "required",
        "values": ["apprenticeship"],
    }
    assert (run["results_raw"], run["targets_created"], run["items_rejected"]) == (2, 1, 1)
    assert run["item_errors"] == 0
    assert client.get(f"/api/search-runs/{run['id']}").json()["id"] == run["id"]
    assert [r["id"] for r in client.get("/api/search-runs").json()] == [run["id"]]


def test_an_item_that_fails_does_not_cancel_the_others(
    client: TestClient,
    db_session: Session,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.errors import UnprocessableError

    original = TargetService.create_target

    def flaky(self: TargetService, data: Any, **kwargs: Any) -> Any:
        if data.company is not None and data.company.name == "Broken Corp":
            raise UnprocessableError("refused")
        return original(self, data, **kwargs)

    monkeypatch.setattr(TargetService, "create_target", flaky)
    install(
        client,
        web=FakeWebProvider(
            [
                hit("Analyst at Fixture Corp", "https://search.example.invalid/1"),
                hit("Analyst at Broken Corp", "https://search.example.invalid/2"),
                hit("Analyst at Other Corp", "https://search.example.invalid/3"),
            ]
        ),
    )

    run = ok(client)

    assert run["status"] == "completed_with_errors"
    assert (run["targets_created"], run["item_errors"]) == (2, 1)
    assert [e["code"] for e in run["errors"]] == ["item_errors"]
    rows = run_of(client, run["id"])["items"]
    assert [r["outcome"] for r in rows] == ["target_created", "error", "target_created"]
    assert rows[1]["reason"] == "ingestion_refused" and rows[1]["target_id"] is None
    assert {c.name for c in db_session.scalars(select(Company))} == {"Fixture Corp", "Other Corp"}


def test_a_provider_failure_is_a_failed_run_never_an_empty_success(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider(error=ProviderErrorCode.RATE_LIMITED))

    run = ok(client)

    assert run["status"] == "failed" and run["finished_at"]
    assert run["errors"] == [{"code": "provider_error", "detail": "rate_limited"}]
    assert (run["results_raw"], run["targets_created"], run["items_rejected"]) == (0, 0, 0)
    assert count(db_session, Target) == 0
    assert run_of(client, run["id"])["items"] == []


def test_an_offer_source_failure_is_a_failed_run_too(
    client: TestClient, profile: dict[str, Any]
) -> None:
    install(client, offers=FakeOfferSource(error=ProviderErrorCode.UNAVAILABLE))

    run = ok(client, provider=BOARD)

    assert run["status"] == "failed"
    assert run["errors"] == [{"code": "provider_error", "detail": "unavailable"}]


def test_an_incomplete_result_is_never_reported_as_a_clean_run(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit()], completed=False))
    partial = ok(client)
    install(client, web=FakeWebProvider([], completed=False))
    empty_incomplete = ok(client)
    install(client, web=FakeWebProvider([], completed=True))
    empty_complete = ok(client)

    assert partial["status"] == "completed_with_errors" and partial["targets_created"] == 1
    assert [e["code"] for e in partial["errors"]] == ["provider_incomplete"]
    assert empty_incomplete["status"] == "failed"  # nothing found AND not complete: no conclusion
    assert empty_complete["status"] == "completed" and empty_complete["results_raw"] == 0


def test_results_beyond_the_requested_limit_are_ignored_and_reported(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    hits = [hit(f"Analyst at Corp {n}", f"https://search.example.invalid/{n}") for n in range(5)]
    install(client, web=FakeWebProvider(hits))

    run = ok(client, max_results=2)

    assert (run["results_raw"], run["targets_created"]) == (5, 2)
    assert [e["code"] for e in run["errors"]] == ["provider_exceeded_limit"]
    assert run["status"] == "completed_with_errors" and count(db_session, Target) == 2


def test_rejected_items_are_counted_and_kept_with_their_reason(
    client: TestClient, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [
                hit(),
                hit("Nothing useful here", "https://search.example.invalid/x"),
                hit(
                    "Analyst at Bad Corp",
                    "https://search.example.invalid/y",
                    company_website="nope",
                ),
            ]
        ),
    )

    run = ok(client)

    assert (run["results_raw"], run["targets_created"], run["items_rejected"]) == (3, 1, 2)
    rows = run_of(client, run["id"])["items"]
    assert [(r["position"], r["outcome"], r["reason"]) for r in rows] == [
        (0, "target_created", None),
        (1, "rejected", "missing_company_name"),
        (2, "rejected", "invalid_field"),
    ]
    assert rows[2]["fields"] == ["company.website_url"]  # the field name, never its value
    assert "nope" not in str(rows)


def test_a_run_is_on_record_as_running_while_the_provider_works_then_finished(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    seen: list[str] = []

    def peek(query: Any) -> None:
        with Session(db_session.get_bind()) as other:
            run = other.scalars(select(SearchRun)).one()
            seen.append(f"{run.status.value}/{run.finished_at is None}")

    install(client, web=FakeWebProvider([hit()], on_call=peek))

    run = ok(client)

    assert seen == ["running/True"]  # committed before the provider was called
    assert (run["status"], run["finished_at"] is not None) == ("completed", True)


def test_a_running_run_cannot_have_an_end_time_nor_a_finished_one_lack_it(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit()]))
    run = ok(client)

    for statement in (
        "UPDATE search_runs SET status = 'running'",  # finished but marked running
        "UPDATE search_runs SET finished_at = NULL",  # not running but no end
        "UPDATE search_runs SET targets_created = -1",
    ):
        with pytest.raises(DatabaseError):
            db_session.execute(text(statement))
        db_session.rollback()
    assert run["status"] == "completed"


def test_an_unexpected_provider_exception_is_not_swallowed_and_the_run_is_failed(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider(raises=RuntimeError(f"boom {SENTINEL}")))

    with pytest.raises(RuntimeError, match="boom"):
        start(client)

    db_session.expire_all()
    run = db_session.scalars(select(SearchRun)).one()
    assert run.status.value == "failed" and run.finished_at is not None
    assert run.errors == [{"code": "unexpected_error"}]  # a code: never the message
    assert SENTINEL not in str(run.errors) and count(db_session, Target) == 0


def test_an_unexpected_ingestion_bug_rolls_the_whole_run_back(
    client: TestClient,
    db_session: Session,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = TargetService.create_target
    calls: list[int] = []

    def buggy(self: TargetService, data: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise ZeroDivisionError
        return original(self, data, **kwargs)

    monkeypatch.setattr(TargetService, "create_target", buggy)
    install(
        client,
        web=FakeWebProvider(
            [
                hit("Analyst at A Corp", "https://x.example.invalid/1"),
                hit("Analyst at B Corp", "https://x.example.invalid/2"),
            ]
        ),
    )

    with pytest.raises(ZeroDivisionError):
        start(client)

    db_session.expire_all()
    assert db_session.scalars(select(SearchRun)).one().status.value == "failed"
    assert count(db_session, Target) == count(db_session, Company) == 0  # atomic: nothing half-done


def test_the_run_is_audited_with_counters_only(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit(), hit("nothing", "https://x.example.invalid/n")]))

    run = ok(client)

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.SOURCING_RUN)
    ).one()
    assert event.subject == f"search_run:{run['id']}"
    assert event.details == {"mode": "offers", "rows": 2, "created": 1, "matched": 0, "rejected": 1}


def test_nothing_from_the_provider_payload_or_credentials_is_stored(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [
                hit(
                    snippet="s" * 900,
                    authorization=f"Bearer {SENTINEL}",
                    api_key=SENTINEL,
                    raw_payload=SENTINEL,
                )
            ]
        ),
    )

    run = ok(client)

    dump = str(run_of(client, run["id"]))
    for table in ("search_runs", "search_run_items", "audit_events", "sources"):
        rows = db_session.execute(text(f"SELECT * FROM {table}")).all()  # noqa: S608
        dump += str(rows)
    assert SENTINEL not in dump
    item_row = run_of(client, run["id"])["items"][0]
    assert len(item_row["excerpt"]) == 500  # bounded, not the provider's response


# --- D. Qualification integration -------------------------------------------------------------


def test_sourced_targets_are_qualified_by_the_existing_engine(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit("Data Analyst Intern at Fixture Corp")]))
    run = ok(client)
    target_id = run_of(client, run["id"])["items"][0]["target_id"]

    data = qualification(client, target_id)

    assert data["profile_id"] == profile["id"] and data["stale"] is False
    outcomes = {r["criterion"]["dimension"]: r["outcome"] for r in data["results"]}
    assert outcomes == {"contract_type": "unknown", "role": "satisfied"}  # nothing was invented
    assert data["status"] == "needs_information"  # the offer does not state its contract
    assert data["requirements_total"] == 0  # nothing is extracted behind the user's back


def test_requirement_gaps_never_exclude_a_sourced_target_and_stale_rules_still_apply(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        offers=FakeOfferSource([item(description="Kubernetes is required.")]),
    )
    first = ok(client, provider=BOARD)
    target_id = run_of(client, first["id"])["items"][0]["target_id"]
    extract(client, target_id)  # explicit 3b trigger, as ever
    assert qualification(client, target_id)["stale"] is True  # requirements are inputs (3b)

    second = ok(client, provider=BOARD)  # sourcing re-qualifies the existing target
    assert (second["targets_existing"], second["qualifications_created"]) == (1, 1)

    data = qualification(client, target_id)
    assert (data["requirements_total"], data["requirements_gap"]) == (1, 1)
    assert data["status"] != "excluded"  # a gap is "not established", never an exclusion
    assert data["stale"] is False
    brief = client.get(f"/api/targets/{target_id}/personalization-brief").json()
    assert brief["qualification"]["stale"] is False
    add_skill(client, "Kubernetes", "known")  # the Brain changed: the 3b stale rule is unchanged
    assert (
        client.get(f"/api/targets/{target_id}/personalization-brief").json()["qualification"][
            "stale"
        ]
        is True
    )


def test_sourcing_does_not_modify_an_existing_target_company_or_offer(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    manual = client.post(
        "/api/targets",
        json={
            "company": {"name": "Fixture Corp", "sector": "Manual sector"},
            "opportunity": {
                "title": "Data Analyst Intern",
                "url": "https://search.example.invalid/offers/1",
            },
            "relevance_note": "typed by me",
        },
    ).json()["target"]
    install(client, web=FakeWebProvider([hit(company_sector="Sourced sector")]))

    run = ok(client)

    assert (run["targets_existing"], run["targets_created"]) == (1, 0)
    db_session.expire_all()
    target = db_session.get(Target, manual["id"])
    assert target is not None and target.relevance_note == "typed by me"
    assert target.company.sector == "Manual sector"  # matched, never enriched or overwritten
    assert (count(db_session, Target), count(db_session, Opportunity)) == (1, 1)


# --- E. Security and limits -------------------------------------------------------------------


def test_the_sourcing_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/search-runs"),
        ("get", "/api/search-runs"),
        ("get", "/api/search-runs/1"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "everything", "provider": WEB},  # unknown mode
        {"mode": "offers", "provider": WEB, "max_results": 0},
        {"mode": "offers", "provider": WEB, "max_results": 51},
        {"mode": "offers", "provider": "Not A Valid Id!"},
        {"mode": "offers", "provider": ""},
        {"mode": "offers"},
    ],
)
def test_invalid_parameters_are_refused_before_any_provider_is_called(
    client: TestClient, db_session: Session, profile: dict[str, Any], body: dict[str, Any]
) -> None:
    provider = FakeWebProvider([hit()])
    install(client, web=provider)

    response = client.post("/api/search-runs", json=body)

    assert response.status_code == 422
    assert provider.queries == [] and count(db_session, SearchRun) == 0


def test_a_run_that_cannot_be_validated_never_reaches_the_provider(
    client: TestClient, db_session: Session
) -> None:
    web, board = FakeWebProvider([hit()]), FakeOfferSource([item()])
    install(client, web=web, offers=board)

    # no active profile
    assert start(client).status_code == 404
    # a profile with nothing to search for (only a held hard constraint is not searchable)
    make_profile(client, [crit("constraint", ["salary"], "required")], name="Empty")
    assert start(client).status_code == 422
    make_profile(client, [crit("role", ["analyst"], "preferred")], name="Good", is_active=True)
    # unknown profile, unknown provider, an offer source in companies mode
    assert start(client, profile_id=999).status_code == 404
    assert start(client, provider="nobody").status_code == 422
    assert start(client, mode="companies", provider=BOARD).status_code == 422

    assert web.queries == [] and board.queries == [] and count(db_session, SearchRun) == 0


def test_without_any_configured_provider_every_run_is_refused(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
        __import__("app.api.sourcing", fromlist=["get_providers"]).get_providers, None
    )

    response = start(client)

    assert response.status_code == 422 and count(db_session, SearchRun) == 0


def test_the_query_only_carries_search_terms_of_the_profile(
    client: TestClient, profile: dict[str, Any]
) -> None:
    provider = FakeWebProvider([])
    install(client, web=provider)

    ok(client, max_results=7)

    (query,) = provider.queries
    assert (query.mode.value, query.profile_id, query.max_results) == ("offers", profile["id"], 7)
    assert {c.dimension.value for c in query.criteria} == {"contract_type", "role"}
    assert query.values_for(query.criteria[1].dimension) == ("analyst",)
    assert "@" not in repr(query)  # no candidate contact data travels with a query


def test_nothing_touches_the_network(
    client: TestClient, profile: dict[str, Any], no_network: None
) -> None:
    install(client, web=FakeWebProvider([hit(), company_hit()]))

    assert start(client).status_code == 201
    assert start(client, mode="companies").status_code == 201


def test_no_llm_or_third_party_http_library_leaks_into_sourcing() -> None:
    """Perplexity is now a real, deliberately-wired `WebSearchProvider` (its own adapter file may
    reference it) - but no LLM chat provider and no third-party HTTP library ever does, anywhere
    in the sourcing domain."""
    from pathlib import Path

    forbidden = ("openrouter", "anthropic", "openai", "httpx", "requests", "urllib3")
    files = [
        *Path("app/integrations/sourcing").rglob("*.py"),
        Path("app/services/sourcing.py"),
        Path("app/services/hit_extraction.py"),
        Path("app/services/offers_research.py"),
        Path("app/api/sourcing.py"),
    ]
    for file in files:
        source = file.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert f"import {word}" not in source and f"from {word}" not in source, (file, word)


def test_the_provider_registry_is_empty_without_explicit_configuration() -> None:
    """`default_providers()` (the bare registry) and the API's own `get_providers` dependency
    (with `RESEARCH_ENABLED` unset, the default) both give an empty registry: sourcing stays
    refused until someone explicitly turns the Perplexity capability on."""
    from app.core.config import Settings
    from app.core.secrets import PERPLEXITY_API_KEY, InMemorySecretStore
    from app.integrations.sourcing.ports import default_providers
    from app.services.sourcing import default_web_search_provider

    registry = default_providers()
    assert dict(registry.web) == {} and dict(registry.offers) == {}

    secrets = InMemorySecretStore()
    secrets.set(PERPLEXITY_API_KEY, "pplx-fixture")
    assert (
        default_web_search_provider(Settings(), secrets) is None
    )  # research_enabled defaults False


def test_items_are_returned_as_sourced_items_of_a_single_type() -> None:
    assert isinstance(item(), SourcedItem)


# --- F. Regression ----------------------------------------------------------------------------


def test_manual_targets_and_company_reads_expose_the_new_status_without_change(
    client: TestClient, profile: dict[str, Any]
) -> None:
    created = client.post("/api/targets", json={"company": {"name": "Fixture Corp"}}).json()

    company = created["target"]["company"]
    assert company["offers_research"] == "not_started" and company["offers_research_at"] is None
    assert company["contact_research"] == "not_started"
    assert created["created"] is True
