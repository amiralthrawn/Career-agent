"""Research batch orchestration (step 7): 500 opportunities must not mean 500 Perplexity calls.

Scenario mirrors the brief exactly (all synthetic, one shared profile: sector=fintech required,
contract_type=apprenticeship required, location=Faketown preferred):

- Target A: everything known and matching -> candidate, no research.
- Target B: sector unknown (required) -> needs_information -> researched -> candidate.
- Target C: contract_type known incompatible -> excluded, no research.
- Target D: only the preferred location is unknown -> candidate, no mandatory research.
- Target E: sector unknown (required), but the provider fails for its company -> stays
  needs_information, `research_failed` recorded explicitly.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.research.ports import (
    Observation,
    ResearchError,
    ResearchErrorCode,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
    ResearchSubject,
)
from app.models import AuditEvent, Company, CompanyResearchFact, Qualification
from app.models.audit import AuditEventType
from app.services.research_batch import ResearchBatchService, ResearchOutcome
from tests.qualification_factory import add_target, crit, make_profile, qualification


class ScriptedProvider:
    """A fake `ResearchProvider`: one scripted outcome per company name; records every query."""

    def __init__(self, script: dict[str, ResearchResult | ResearchErrorCode]) -> None:
        self._script = script
        self.asked: list[ResearchQuery] = []

    def research(self, query: ResearchQuery) -> ResearchResult:
        self.asked.append(query)
        outcome = self._script[query.subject.company_name]
        if isinstance(outcome, ResearchErrorCode):
            raise ResearchError(outcome)
        return outcome


def result(claim: str = "A fast-growing fintech company.") -> ResearchResult:
    return ResearchResult(
        provider="perplexity",
        model="fake",
        query=ResearchQuery(objective="obj", subject=ResearchSubject(company_name="placeholder")),
        status=ResearchStatus.OK,
        observations=(Observation(claim=claim, source_url="https://x.invalid/fintech"),),
    )


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture
def profile(client: TestClient) -> dict[str, Any]:
    return make_profile(
        client,
        [
            crit("sector", ["fintech"], "required"),
            crit("contract_type", ["apprenticeship"], "required"),
            crit("location", ["Faketown"], "preferred"),
        ],
    )


def make(client: TestClient, letter: str, **overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        domain=f"company-{letter.lower()}.example.invalid",
        name=f"Company {letter}",
        contract_type="apprenticeship",
    )
    return add_target(client, **{**defaults, **overrides})


def scenario(client: TestClient) -> dict[str, dict[str, Any]]:
    return {
        "A": make(client, "A", company={"sector": "fintech"}),  # sector known + matching
        "B": make(client, "B", company={"sector": None}),  # sector unknown, researchable
        "C": make(client, "C", contract_type="internship"),  # known incompatible
        "D": make(  # only the preferred location is unknown
            client, "D", company={"sector": "fintech", "location": None}
        ),
        "E": make(client, "E", company={"sector": None}),  # unknown, but research fails
    }


def status_of(client: TestClient, target_id: int) -> str:
    data = qualification(client, target_id)
    status: str = data["status"]
    return status


def counts(session: Session) -> dict[str, int]:
    return {
        "facts": session.scalar(select(func.count()).select_from(CompanyResearchFact)) or 0,
        "qualifications": session.scalar(select(func.count()).select_from(Qualification)) or 0,
    }


# --- The full brief scenario --------------------------------------------------------------------


def test_the_full_scenario_researches_only_what_can_change_the_qualification(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider(
        {
            "Company B": result("A fast-growing fintech company."),
            "Company E": ResearchErrorCode.UNAVAILABLE,
        }
    )

    report = ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    by_id = {item.target_id: item for item in report.items}
    a, b, c, d, e = (targets[letter]["id"] for letter in "ABCDE")

    assert by_id[a].initial_status.value == "candidate"
    assert by_id[a].research_outcome is ResearchOutcome.NOT_NEEDED

    assert by_id[b].initial_status.value == "needs_information"
    assert by_id[b].research_outcome is ResearchOutcome.RESEARCHED
    assert by_id[b].requalified is True
    assert by_id[b].final_status.value == "candidate"

    assert by_id[c].initial_status.value == "excluded"
    assert by_id[c].research_outcome is ResearchOutcome.NOT_NEEDED
    assert by_id[c].final_status.value == "excluded"

    assert by_id[d].final_status.value == "candidate"
    assert by_id[d].research_outcome is ResearchOutcome.NOT_NEEDED

    assert by_id[e].initial_status.value == "needs_information"
    assert by_id[e].research_outcome is ResearchOutcome.RESEARCH_FAILED
    assert by_id[e].requalified is False
    assert by_id[e].final_status.value == "needs_information"  # never silently unknown -> false

    # Only B and E ever justified a call: A/C/D never even built a plan.
    asked_companies = {q.subject.company_name for q in provider.asked}
    assert asked_companies == {"Company B", "Company E"}
    assert report.plans_built == 2 and report.plans_researched == 1 and report.plans_failed == 1
    assert report.requalified == 1

    # The API confirms the same, final statuses.
    assert status_of(client, a) == "candidate"
    assert status_of(client, b) == "candidate"
    assert status_of(client, c) == "excluded"
    assert status_of(client, d) == "candidate"
    assert status_of(client, e) == "needs_information"


def test_no_candidate_data_ever_reaches_the_provider(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": ResearchErrorCode.UNAVAILABLE})

    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    for query in provider.asked:
        dump = f"{query.objective} {query.focus_areas}"
        assert "fintech" not in dump.lower()  # the candidate's criterion VALUE never travels
        assert "candidate" not in dump.lower()


# --- Company-level grouping (many opportunities, few searches) ---------------------------------


def test_two_targets_of_the_same_company_share_one_research_call(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    first = add_target(client, domain="shared.example.invalid", company={"sector": None})
    second = add_target(
        client,
        domain="shared.example.invalid",
        opportunity={"url": "https://shared.example.invalid/jobs/2", "title": "Other role"},
    )
    provider = ScriptedProvider({"Fixture Corp": result("A fintech company.")})

    report = ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    assert len(provider.asked) == 1  # one company, one call, regardless of target count
    assert report.plans_built == 1 and report.requalified == 2
    assert status_of(client, first["id"]) == "candidate"
    assert status_of(client, second["id"]) == "candidate"


# --- Budget ------------------------------------------------------------------------------------


def test_a_zero_budget_never_calls_the_provider(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": result()})

    report = ResearchBatchService(db_session, provider).run(profile["id"])  # default budget = 0

    assert provider.asked == []
    assert report.plans_skipped_budget == 2 and report.plans_researched == 0
    assert status_of(client, targets["B"]["id"]) == "needs_information"


def test_the_budget_limits_how_many_companies_are_researched(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": result()})

    report = ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=1)

    assert len(provider.asked) == 1
    assert report.plans_researched == 1 and report.plans_skipped_budget == 1
    statuses = {status_of(client, targets[letter]["id"]) for letter in ("B", "E")}
    assert statuses == {"candidate", "needs_information"}  # exactly one flipped


def test_without_a_configured_provider_everything_is_skipped_by_budget(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)

    report = ResearchBatchService(db_session, None).run(profile["id"], max_research_calls=99)

    assert report.plans_skipped_budget == 2 and report.plans_researched == 0
    assert status_of(client, targets["B"]["id"]) == "needs_information"


def test_companies_affecting_more_targets_are_prioritised(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    add_target(client, domain="busy.example.invalid", company={"sector": None})
    add_target(
        client,
        domain="busy.example.invalid",
        opportunity={"url": "https://busy.example.invalid/jobs/2", "title": "Other role"},
    )
    add_target(client, domain="quiet.example.invalid", company={"sector": None})
    provider = ScriptedProvider({"Fixture Corp": result(), "Other Corp": result()})

    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=1)

    (asked,) = provider.asked
    assert asked.subject.company_name == "Fixture Corp"  # affects 2 targets, researched first


# --- Idempotence, isolation, provenance, no score -----------------------------------------------


def test_idempotence_across_two_runs_including_after_a_provider_failure(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    """Two SUCCESSIVE runs of the same batch: a company whose research FAILED is not
    remembered as resolved (it is planned and asked again); a company that already SUCCEEDED
    is not re-researched, and its qualification stays the exact same row (true idempotence)."""
    targets = scenario(client)
    run1 = ScriptedProvider({"Company B": ResearchErrorCode.TIMEOUT, "Company E": result()})

    ResearchBatchService(db_session, run1).run(profile["id"], max_research_calls=10)

    assert status_of(client, targets["B"]["id"]) == "needs_information"  # B's failure
    b_qualification_after_run1 = qualification(client, targets["B"]["id"])["id"]
    e_qualification_after_run1 = qualification(client, targets["E"]["id"])["id"]
    facts_after_run1 = counts(db_session)["facts"]

    # Run 2: B now succeeds; E is not scripted at all - if it were asked again, the
    # `ScriptedProvider`'s dict lookup would raise `KeyError` and fail this test.
    run2 = ScriptedProvider({"Company B": result("A fast-growing fintech company.")})
    ResearchBatchService(db_session, run2).run(profile["id"], max_research_calls=10)

    # B: retried, now resolved - a NEW qualification, not the one from run 1.
    assert [q.subject.company_name for q in run2.asked] == ["Company B"]
    assert status_of(client, targets["B"]["id"]) == "candidate"
    assert qualification(client, targets["B"]["id"])["id"] != b_qualification_after_run1

    # E: already resolved in run 1 - never asked again, same qualification row reused.
    assert status_of(client, targets["E"]["id"]) == "candidate"
    assert qualification(client, targets["E"]["id"])["id"] == e_qualification_after_run1

    # Run 1's failure left no phantom fact; run 2 added exactly one new, real one.
    assert counts(db_session)["facts"] == facts_after_run1 + 1


def test_a_successful_requalification_is_itself_idempotent(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": ResearchErrorCode.OTHER})
    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)
    first_id = qualification(client, targets["B"]["id"])["id"]

    # B no longer has any need (sector now known): a second run researches nothing new for it.
    provider2 = ScriptedProvider({"Company E": ResearchErrorCode.OTHER})
    ResearchBatchService(db_session, provider2).run(profile["id"], max_research_calls=10)

    assert qualification(client, targets["B"]["id"])["id"] == first_id  # same qualification, reused
    assert all(q.subject.company_name != "Company B" for q in provider2.asked)


def test_one_provider_failure_never_loses_the_others_results(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": ResearchErrorCode.UNAVAILABLE, "Company E": result()})

    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    assert status_of(client, targets["B"]["id"]) == "needs_information"  # its own failure
    assert status_of(client, targets["E"]["id"]) == "candidate"  # unaffected by B's failure


def test_accepted_facts_keep_their_provenance_and_are_never_the_companys_own_field(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider(
        {"Company B": result("A fintech company."), "Company E": ResearchErrorCode.OTHER}
    )

    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    fact = db_session.scalars(select(CompanyResearchFact)).one()
    assert fact.claim == "A fintech company." and fact.source_url == "https://x.invalid/fintech"
    company = db_session.get(Company, targets["B"]["company"]["id"])
    assert company is not None and company.sector is None  # never rewritten


def test_the_batch_report_and_qualification_hold_no_score_or_rank(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": ResearchErrorCode.OTHER})

    report = ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    dump = str(report).lower()
    for forbidden in ("score", "match_score", "confidence", "ai_score", "rank"):
        assert forbidden not in dump


def test_the_batch_run_is_audited_with_counters_only(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": ResearchErrorCode.OTHER})

    ResearchBatchService(db_session, provider).run(
        profile["id"], max_research_calls=10, actor="cli"
    )

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.RESEARCH_BATCH_RUN)
    ).one()
    assert event.actor == "cli"
    assert set(event.details) <= {"rows", "created", "matched", "rejected"}


def test_max_targets_bounds_how_many_targets_are_even_considered(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": result(), "Company E": result()})

    report = ResearchBatchService(db_session, provider).run(
        profile["id"], max_targets=1, max_research_calls=10
    )

    assert report.targets_processed == 1
    first_id = min(t["id"] for t in targets.values())
    assert report.items[0].target_id == first_id


def test_explicit_target_ids_scope_the_batch_to_exactly_those_targets(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    provider = ScriptedProvider({"Company B": result()})

    report = ResearchBatchService(db_session, provider).run(
        profile["id"], target_ids=[targets["B"]["id"]], max_research_calls=10
    )

    assert report.targets_processed == 1
    assert {item.target_id for item in report.items} == {targets["B"]["id"]}


# --- Technical review follow-ups ----------------------------------------------------------------


def test_expiring_cache_after_research_is_targeted_never_a_blanket_session_wide_expire(
    client: TestClient,
    db_session: Session,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`session.expire_all()` would also be *correct* (it forces every stale relationship to
    reload), but it is wasteful: in a large batch it would force a reload of every OTHER
    target's data too, not just the handful of companies that were actually researched. This
    guards against silently regressing back to the blanket call."""
    targets = scenario(client)
    provider = ScriptedProvider(
        {
            "Company B": result("A fast-growing fintech company."),
            "Company E": ResearchErrorCode.OTHER,
        }
    )

    def _fail_if_called() -> None:
        raise AssertionError("expire_all() must not be called: expire only what was researched")

    monkeypatch.setattr(db_session, "expire_all", _fail_if_called)

    ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    # The fix still works: B's requalification correctly sees the newly accepted fact.
    assert status_of(client, targets["B"]["id"]) == "candidate"


def test_an_observation_without_a_source_url_is_never_stored_or_used(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    targets = scenario(client)
    unsourced_only = ResearchResult(
        provider="perplexity",
        model="fake",
        query=ResearchQuery(objective="obj", subject=ResearchSubject(company_name="placeholder")),
        status=ResearchStatus.OK,
        # No `source_url`: exactly what a provider must never let through, but the sink checks
        # it too rather than trusting every current and future adapter to.
        observations=(Observation(claim="A fintech company, allegedly.", source_url=None),),
    )
    provider = ScriptedProvider({"Company B": unsourced_only, "Company E": ResearchErrorCode.OTHER})
    before = counts(db_session)["facts"]

    report = ResearchBatchService(db_session, provider).run(profile["id"], max_research_calls=10)

    by_id = {item.target_id: item for item in report.items}
    assert (
        by_id[targets["B"]["id"]].research_outcome is ResearchOutcome.RESEARCHED
    )  # call succeeded
    assert counts(db_session)["facts"] == before  # but nothing unsourced was persisted
    # Nothing was accepted, so re-qualifying found nothing new: still needs_information.
    assert status_of(client, targets["B"]["id"]) == "needs_information"
