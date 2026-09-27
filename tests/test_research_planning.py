"""Information needs and research plans (step 7): why research, what for, and only when it can
change the qualification. All synthetic, no network, no real Perplexity call.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.research.ports import Observation, ResearchResult, ResearchStatus
from app.models import Company, CompanyResearchFact, Qualification, Target
from app.models.enums import CriterionDimension
from app.services.research_planning import (
    NeedPriority,
    accept_observations,
    information_needs,
    plan_for_company,
)
from tests.qualification_factory import add_target, crit, make_profile, other_target, qualify


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def qualified(
    client: TestClient, db_session: Session, target_json: dict[str, Any]
) -> tuple[Target, Qualification]:
    response = qualify(client, target_json["id"])
    assert response.status_code in (200, 201), response.text
    target = db_session.get(Target, target_json["id"])
    assert target is not None
    qualification = db_session.scalars(
        select(Qualification).where(Qualification.target_id == target.id)
    ).one()
    return target, qualification


def fixed_result(**overrides: Any) -> ResearchResult:
    from tests.test_company_research import FIXTURE_QUERY

    defaults: dict[str, Any] = dict(
        provider="perplexity",
        model="fake",
        query=FIXTURE_QUERY,
        status=ResearchStatus.OK,
        observations=(Observation(claim="A fintech scale-up.", source_url="https://x.invalid/a"),),
    )
    return ResearchResult(**{**defaults, **overrides})


# --- information_needs -------------------------------------------------------------------------


def test_a_required_unknown_sector_is_a_blocking_need(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["fintech"], "required")])
    target, qualification = qualified(
        client, db_session, add_target(client, company={"sector": None})
    )

    (need,) = information_needs(target, qualification)

    assert need.dimension is CriterionDimension.SECTOR
    assert need.priority is NeedPriority.BLOCKING
    assert "unknown" in need.reason and "required" in need.reason
    assert need.target_id == target.id


def test_a_preferred_unknown_sector_is_informational_only(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["fintech"], "preferred")])
    target, qualification = qualified(
        client, db_session, add_target(client, company={"sector": None})
    )

    (need,) = information_needs(target, qualification)

    assert need.priority is NeedPriority.INFORMATIONAL


def test_a_known_sector_gives_no_need_at_all(client: TestClient, db_session: Session) -> None:
    make_profile(client, [crit("sector", ["Synthetic software"], "required")])
    target, qualification = qualified(client, db_session, add_target(client))  # sector is set

    assert information_needs(target, qualification) == []


def test_a_not_matched_sector_gives_no_need_the_data_already_answered(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["fintech"], "required")])
    target, qualification = qualified(client, db_session, add_target(client))  # sector is known

    assert information_needs(target, qualification) == []


def test_an_excluded_target_generates_no_need(client: TestClient, db_session: Session) -> None:
    make_profile(client, [crit("contract_type", ["internship"], "required")])
    target, qualification = qualified(
        client, db_session, add_target(client, contract_type="apprenticeship")
    )

    assert qualification.status.value == "excluded"
    assert information_needs(target, qualification) == []


@pytest.mark.parametrize(
    ("dimension", "values"), [("country", ["FR"]), ("contract_type", ["apprenticeship"])]
)
def test_a_dimension_research_cannot_answer_generates_no_need(
    client: TestClient, db_session: Session, dimension: str, values: list[str]
) -> None:
    make_profile(client, [crit(dimension, values, "required")])
    target, qualification = qualified(
        client, db_session, add_target(client, company={"country_code": None}, contract_type=None)
    )

    assert information_needs(target, qualification) == []


def test_role_is_never_researchable_even_though_it_can_be_unknown(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("role", ["analyst"], "required")])
    target, qualification = qualified(client, db_session, add_target(client, offer=False))

    assert information_needs(target, qualification) == []


def test_offer_location_is_not_researchable_but_company_location_is(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("location", ["Faketown"], "required")])
    with_offer, q_with_offer = qualified(
        client, db_session, add_target(client, opportunity={"location": None})
    )
    spontaneous, q_spontaneous = qualified(
        client, db_session, other_target(client, offer=False, company={"location": None})
    )

    assert information_needs(with_offer, q_with_offer) == []
    (need,) = information_needs(spontaneous, q_spontaneous)
    assert need.dimension is CriterionDimension.LOCATION


def test_needs_are_deduplicated_by_dimension_when_building_a_plan(
    client: TestClient, db_session: Session
) -> None:
    make_profile(
        client,
        [crit("sector", ["fintech"], "required"), crit("keyword", ["python"], "required")],
    )
    target, qualification = qualified(
        client,
        db_session,
        add_target(client, company={"sector": None}, opportunity={"description_text": None}),
    )
    needs = information_needs(target, qualification)

    company = db_session.get(Company, target.company_id)
    assert company is not None
    plan = plan_for_company(company, needs)

    assert plan is not None
    assert len(plan.query.focus_areas) == 2  # one entry per distinct researchable dimension
    assert plan.company_id == company.id


def test_no_needs_gives_no_plan(client: TestClient, db_session: Session) -> None:
    company = db_session.get(Company, add_target(client)["company"]["id"])
    assert company is not None

    assert plan_for_company(company, []) is None


def test_the_query_never_carries_the_candidates_criteria_values(
    client: TestClient, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["a very specific niche sector"], "required")])
    target, qualification = qualified(
        client, db_session, add_target(client, company={"sector": None})
    )
    company = db_session.get(Company, target.company_id)
    assert company is not None

    plan = plan_for_company(company, information_needs(target, qualification))

    assert plan is not None
    dump = f"{plan.query.objective} {plan.query.focus_areas}"
    assert "very specific niche sector" not in dump  # only the DIMENSION name travels, not values


# --- accept_observations -------------------------------------------------------------------


def test_accepted_observations_keep_full_provenance(
    client: TestClient, db_session: Session
) -> None:
    target = add_target(client)
    company_id = target["company"]["id"]

    facts = accept_observations(db_session, company_id, fixed_result())
    db_session.commit()

    (fact,) = facts
    assert fact.claim == "A fintech scale-up." and fact.source_url == "https://x.invalid/a"
    stored = db_session.scalars(
        select(CompanyResearchFact).where(CompanyResearchFact.company_id == company_id)
    ).one()
    assert stored.id == fact.id


def test_accepting_never_touches_the_companys_own_fields(
    client: TestClient, db_session: Session
) -> None:
    target = add_target(client)
    company_id = target["company"]["id"]
    before = db_session.get(Company, company_id)
    assert before is not None
    original_sector = before.sector

    accept_observations(db_session, company_id, fixed_result())
    db_session.commit()

    db_session.expire_all()
    after = db_session.get(Company, company_id)
    assert after is not None and after.sector == original_sector


def test_an_observation_without_a_source_url_is_dropped_not_stored(
    client: TestClient, db_session: Session
) -> None:
    target = add_target(client)
    company_id = target["company"]["id"]
    mixed = fixed_result(
        observations=(
            Observation(claim="Sourced.", source_url="https://x.invalid/ok"),
            Observation(claim="Unsourced - never stored.", source_url=None),
        )
    )

    facts = accept_observations(db_session, company_id, mixed)
    db_session.commit()

    assert [f.claim for f in facts] == ["Sourced."]
    stored = db_session.scalars(
        select(CompanyResearchFact).where(CompanyResearchFact.company_id == company_id)
    ).all()
    assert [f.claim for f in stored] == ["Sourced."]
