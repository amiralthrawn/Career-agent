"""`--mode all` (step 3c): both sourcing flows from ONE provider call, in ONE `SearchRun`.

Reuses the exact same `SourcingService`/`HitExtractor`/dedup/qualification as `offers` and
`companies` - no second engine. Fictional providers only, no network.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableError
from app.models import Company, Target
from app.services.targets import TargetService
from tests.qualification_factory import crit, make_profile
from tests.sourcing_fakes import FakeWebProvider, company_hit, hit, install, run_of, start


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture
def profile(client: TestClient) -> dict[str, Any]:
    return make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])


def ok(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    response = start(client, mode="all", **kwargs)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


# --- offers / companies (unaffected by `all` existing) -----------------------------------------


def test_offers_mode_breakdown_stays_empty(client: TestClient, profile: dict[str, Any]) -> None:
    install(client, web=FakeWebProvider([hit()]))
    response = start(client, mode="offers")
    assert response.status_code == 201
    assert response.json()["breakdown"] == {}


def test_companies_mode_breakdown_stays_empty(client: TestClient, profile: dict[str, Any]) -> None:
    install(client, web=FakeWebProvider([company_hit()]))
    response = start(client, mode="companies")
    assert response.status_code == 201
    assert response.json()["breakdown"] == {}


# --- all: single flows -------------------------------------------------------------------------


def test_all_mode_an_offer_shaped_hit_becomes_an_offer_target(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([hit()]))

    run = ok(client)

    assert run["status"] == "completed"
    assert run["targets_created"] == 1
    assert set(run["breakdown"]) == {"offers"}
    assert run["breakdown"]["offers"]["targets_created"] == 1
    target = db_session.scalars(select(Target)).one()
    assert target.mode == "offer"


def test_all_mode_a_company_only_hit_becomes_a_spontaneous_target(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(client, web=FakeWebProvider([company_hit()]))

    run = ok(client)

    assert run["status"] == "completed"
    assert run["targets_created"] == 1
    assert run["breakdown"]["companies"]["targets_created"] == 1
    assert "offers" not in run["breakdown"]
    target = db_session.scalars(select(Target)).one()
    assert target.mode == "spontaneous" and target.opportunity is None
    assert target.relevance_note is None  # never a claim that the company recruits


# --- all: both flows in one run -----------------------------------------------------------------


def test_all_mode_mixes_both_flows_in_one_run(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    install(
        client,
        web=FakeWebProvider(
            [
                hit("Data Analyst Intern at Fixture Corp", "https://search.example.invalid/o/1"),
                company_hit("Other Corp"),
            ]
        ),
    )

    run = ok(client)

    assert run["status"] == "completed"
    assert run["targets_created"] == 2
    assert run["breakdown"] == {
        "offers": {
            "targets_created": 1,
            "targets_existing": 0,
            "companies_created": 1,
            "opportunities_created": 1,
            "qualifications_created": 1,
        },
        "companies": {
            "targets_created": 1,
            "targets_existing": 0,
            "companies_created": 1,
            "opportunities_created": 0,
            "qualifications_created": 1,
        },
    }
    targets = {t.company.name: t for t in db_session.scalars(select(Target)).all()}
    assert targets["Fixture Corp"].mode == "offer"
    assert targets["Other Corp"].mode == "spontaneous"


def test_all_mode_partial_failure_in_one_flow_does_not_prevent_the_other(
    client: TestClient,
    db_session: Session,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                hit(
                    "Analyst at Broken Corp", "https://search.example.invalid/1"
                ),  # offers-shaped, fails
                company_hit("Fixture Corp"),  # companies-shaped, succeeds
            ]
        ),
    )

    run = ok(client)

    assert run["status"] == "completed_with_errors"
    assert run["item_errors"] == 1 and run["targets_created"] == 1
    assert run["breakdown"]["companies"]["targets_created"] == 1
    assert "offers" not in run["breakdown"]  # the failed offer item never reached the tally
    rows = run_of(client, run["id"])["items"]
    assert [r["outcome"] for r in rows] == ["error", "target_created"]
    assert rows[0]["reason"] == "ingestion_refused"
    assert db_session.scalars(select(Company)).one().name == "Fixture Corp"


# --- all: de-duplication between a spontaneous and an offer target of the SAME company ----------


def test_all_mode_a_spontaneous_and_an_offer_target_coexist_for_the_same_company(
    client: TestClient, db_session: Session, profile: dict[str, Any]
) -> None:
    site = {"company_website": "https://fixture-corp.example.invalid"}
    install(client, web=FakeWebProvider([company_hit(**site), hit(**site)]))

    run = ok(client)

    assert run["status"] == "completed"
    assert run["targets_created"] == 2
    assert run["breakdown"]["companies"]["companies_created"] == 1  # the company itself: once
    assert run["breakdown"]["offers"]["companies_created"] == 0  # matched, not re-created
    assert db_session.scalars(select(Company)).one() is not None  # exactly one company row
    targets = db_session.scalars(select(Target).order_by(Target.id)).all()
    assert [t.mode for t in targets] == ["spontaneous", "offer"]
    assert len({t.company_id for t in targets}) == 1
