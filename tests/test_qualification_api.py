"""Qualification end to end: explainable, deterministic, immutable, never a score."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Company, CriterionResult, Qualification, QualificationReason
from app.models.audit import AuditEventType
from tests.qualification_factory import (
    add_target,
    crit,
    make_profile,
    other_target,
    outcomes,
    qualification,
    qualify,
    reason_codes,
)


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def all_keys(value: Any) -> set[str]:
    """Every key of a JSON document, recursively."""
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in all_keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in all_keys(item)}
    return set()


def all_numbers(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [value]
    if isinstance(value, dict):
        return [n for item in value.values() for n in all_numbers(item)]
    if isinstance(value, list):
        return [n for item in value for n in all_numbers(item)]
    return []


# --- Required criteria -----------------------------------------------------------------


def test_a_satisfied_required_criterion_does_not_exclude(
    client: TestClient, with_candidate: None, no_network: None
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client, contract_type="apprenticeship")

    result = qualify(client, target["id"])

    assert result.status_code == 201 and result.json()["created"] is True
    data = result.json()["qualification"]
    assert data["status"] == "candidate" and data["method"] == "deterministic"
    assert outcomes(data) == {"contract_type": "satisfied"}
    assert reason_codes(data) == ["required_satisfied"]


def test_a_known_incompatible_required_criterion_excludes_but_keeps_the_target(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client, contract_type="full_time")

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "excluded"
    assert outcomes(data) == {"contract_type": "incompatible"}
    assert data["results"][0]["code"] == "not_in_allowed_set"
    assert reason_codes(data) == ["excluded_by_required"]
    assert "Known incompatibility" in data["reasons"][0]["text"]
    # Nothing is deleted or dismissed: excluded targets stay, with their reason.
    kept = client.get(f"/api/targets/{target['id']}").json()
    assert kept["status"] == "new" and kept["id"] == target["id"]
    assert [
        t["id"] for t in client.get("/api/targets", params={"qualification": "excluded"}).json()
    ] == [target["id"]]
    assert qualification(client, target["id"])["status"] == "excluded"


@pytest.mark.parametrize(
    ("criterion", "company", "outcome"),
    [
        (crit("country", ["FR"], "required"), {"country_code": None}, "unknown"),
        (crit("location", ["Paris"], "required"), None, "not_matched"),
        (crit("sector", ["fintech"], "required"), None, "not_matched"),
        (crit("keyword", ["rust"], "required"), None, "not_matched"),
    ],
)
def test_an_unresolved_required_criterion_needs_information_and_never_excludes(
    client: TestClient,
    with_candidate: None,
    criterion: dict[str, Any],
    company: dict[str, Any] | None,
    outcome: str,
) -> None:
    make_profile(client, [criterion])
    target = add_target(client, company=company)

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {criterion["dimension"]: outcome}
    assert data["status"] == "needs_information"  # never "excluded"
    assert reason_codes(data) == ["open_question_required"]


def test_required_country_missing_is_unknown_and_needs_information(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("country", ["FR"], "required")])
    target = add_target(client, company={"country_code": None})

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"country": "unknown"}
    assert data["results"][0]["code"] == "data_missing"
    assert data["status"] == "needs_information"
    assert reason_codes(data) == ["open_question_required"]
    assert "unresolved" in data["reasons"][0]["text"]


def test_required_free_text_without_a_match_is_not_matched_never_a_violation(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("location", ["Paris"], "required")])
    target = add_target(client)  # offer in "Faketown"

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"location": "not_matched"}
    assert data["status"] == "needs_information"  # a suburb is not proof of incompatibility


def test_a_known_incompatibility_wins_over_unresolved_criteria(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("country", ["FR"], "required"),
            crit("contract_type", ["apprenticeship"], "required"),
        ],
    )
    target = add_target(client, company={"country_code": None}, contract_type="full_time")

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"country": "unknown", "contract_type": "incompatible"}
    assert data["status"] == "excluded"


# --- Preferred and flexible criteria ---------------------------------------------------


def test_a_satisfied_preferred_criterion_is_a_reason_and_a_counter(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("sector", ["software"], "preferred"),
        ],
    )
    target = add_target(client)

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate"
    assert reason_codes(data) == ["required_satisfied", "preferred_satisfied"]
    assert data["counters"]["preferred"] == {
        "total": 1,
        "satisfied": 1,
        "incompatible": 0,
        "not_matched": 0,
        "unknown": 0,
    }
    assert data["counters"]["required"]["satisfied"] == 1


@pytest.mark.parametrize(
    "preferred",
    [
        crit("contract_type", ["internship"], "preferred"),  # known incompatibility, only preferred
        crit("sector", ["fintech"], "preferred"),  # no match
        crit("country", ["FR"], "preferred"),  # missing data
        crit("company", ["Nobody Corp"], "preferred"),
    ],
)
def test_preferred_criteria_never_exclude(
    client: TestClient, with_candidate: None, preferred: dict[str, Any]
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required"), preferred])
    target = add_target(client, company={"country_code": None})

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate"
    assert "preferred_satisfied" not in reason_codes(data)
    assert (
        data["counters"]["preferred"]["satisfied"] == 0
        and data["counters"]["preferred"]["total"] == 1
    )


def test_flexible_criteria_have_no_effect_on_the_status(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("keyword", ["rust"], "flexible"),  # not matched
            crit("keyword", ["fintech"], "flexible", operator="none_of"),
            crit("country", ["FR"], "flexible"),
        ],
    )
    target = add_target(
        client, company={"country_code": None}, opportunity={"description_text": None}
    )

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate"
    assert data["counters"]["flexible"]["total"] == 3


def test_a_flexible_match_is_a_reason(client: TestClient, with_candidate: None) -> None:
    make_profile(client, [crit("keyword", ["python"], "flexible")])
    target = add_target(client)

    data = qualify(client, target["id"]).json()["qualification"]

    assert reason_codes(data) == ["flexible_matched"] and data["status"] == "candidate"


# --- none_of ---------------------------------------------------------------------------


def test_none_of_excludes_when_the_excluded_value_is_present(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("sector", ["gambling"], "required", operator="none_of")])
    bad = add_target(client, company={"sector": "Online gambling"})
    good = other_target(client)

    assert qualify(client, bad["id"]).json()["qualification"]["status"] == "excluded"
    data = qualify(client, good["id"]).json()["qualification"]
    assert data["status"] == "candidate" and data["results"][0]["code"] == "excluded_term_absent"


def test_none_of_on_a_blocked_company_and_a_blocked_place(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("company", ["Fixture Corp"], "required", operator="none_of"),
            crit("location", ["Lyon"], "required", operator="none_of"),
        ],
    )
    blocked = add_target(client)
    lyon = other_target(client, opportunity={"location": "Lyon"})
    fine = add_target(client, domain="third.example.invalid", name="Third Corp")

    assert qualify(client, blocked["id"]).json()["qualification"]["status"] == "excluded"
    assert qualify(client, lyon["id"]).json()["qualification"]["status"] == "excluded"
    assert qualify(client, fine["id"]).json()["qualification"]["status"] == "candidate"


def test_none_of_does_not_conclude_when_the_text_is_incomplete(
    client: TestClient, with_candidate: None
) -> None:
    """Without the offer description, the absence of an excluded keyword proves nothing."""
    make_profile(client, [crit("keyword", ["gambling"], "required", operator="none_of")])
    target = add_target(client, opportunity={"description_text": None})

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["results"][0]["code"] == "description_not_provided"
    assert data["status"] == "needs_information"


# --- Spontaneous targets and missing offer data ----------------------------------------


def test_a_spontaneous_target_is_qualified_without_an_opportunity(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("sector", ["software"], "preferred"),
            crit("role", ["data"], "preferred"),  # depends on an offer
        ],
    )
    spontaneous = add_target(client, offer=False)

    data = qualify(client, spontaneous["id"]).json()["qualification"]

    assert spontaneous["mode"] == "spontaneous" and spontaneous["opportunity"] is None
    assert data["status"] == "candidate"
    assert outcomes(data) == {
        "contract_type": "satisfied",
        "sector": "satisfied",
        "role": "unknown",
    }
    role = next(r for r in data["results"] if r["criterion"]["dimension"] == "role")
    assert role["code"] == "no_offer"  # unknown, not a violation
    # Informative, and explicitly not a negative signal.
    assert "no_offer_published" in reason_codes(data)
    text = next(r["text"] for r in data["reasons"] if r["code"] == "no_offer_published")
    assert "nothing about whether the company is hiring" in text


def test_a_required_criterion_that_needs_an_offer_asks_for_information_on_a_spontaneous_target(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("role", ["data"], "required")])
    spontaneous = add_target(client, offer=False)

    data = qualify(client, spontaneous["id"]).json()["qualification"]

    assert data["status"] == "needs_information" and data["results"][0]["code"] == "no_offer"


def test_a_criterion_on_data_missing_from_the_opportunity_is_unknown(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("location", ["Faketown"], "required")])
    target = add_target(client, opportunity={"location": None})  # the company is in Faketown too

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"location": "unknown"}
    assert data["results"][0]["code"] == "data_missing"
    assert data["status"] == "needs_information"


def test_both_flows_share_the_same_qualification_shape(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    with_offer = qualify(client, add_target(client)["id"]).json()["qualification"]
    without = qualify(client, other_target(client, offer=False)["id"]).json()["qualification"]

    assert set(with_offer) == set(without)
    assert set(with_offer["results"][0]) == set(without["results"][0])


# --- remote_mode -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offer_mode", "status", "outcome"),
    [
        ("remote", "candidate", "satisfied"),
        ("hybrid", "candidate", "satisfied"),
        ("onsite", "excluded", "incompatible"),  # stated by the offer: a KNOWN incompatibility
        (None, "needs_information", "unknown"),  # not stated: unknown, never excluded
    ],
)
def test_a_required_remote_mode(
    client: TestClient,
    with_candidate: None,
    offer_mode: str | None,
    status: str,
    outcome: str,
) -> None:
    make_profile(client, [crit("remote_mode", ["remote", "hybrid"], "required")])
    target = add_target(client, opportunity={"remote_mode": offer_mode})

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"remote_mode": outcome} and data["status"] == status
    if offer_mode is None:
        assert data["results"][0]["code"] == "data_missing"
        assert reason_codes(data) == ["open_question_required"]


def test_a_required_remote_mode_on_a_spontaneous_target_needs_information(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("remote_mode", ["remote"], "required")])
    spontaneous = add_target(client, offer=False)

    data = qualify(client, spontaneous["id"]).json()["qualification"]

    assert data["status"] == "needs_information" and data["results"][0]["code"] == "no_offer"


@pytest.mark.parametrize("level", ["preferred", "flexible"])
def test_a_missing_remote_mode_is_informative_only_when_not_required(
    client: TestClient, with_candidate: None, level: str
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("remote_mode", ["remote"], level),
        ],
    )
    target = add_target(client)  # the offer does not state how it is worked

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate" and outcomes(data)["remote_mode"] == "unknown"
    assert data["counters"][level]["unknown"] == 1


def test_a_preferred_remote_mismatch_never_excludes(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("remote_mode", ["remote"], "preferred")])
    target = add_target(client, opportunity={"remote_mode": "onsite"})

    data = qualify(client, target["id"]).json()["qualification"]

    assert outcomes(data) == {"remote_mode": "incompatible"} and data["status"] == "candidate"


def test_remote_mode_values_are_validated_and_optional(
    client: TestClient, with_candidate: None
) -> None:
    assert (
        client.post(
            "/api/search-profiles",
            json={"name": "P", "criteria": [crit("remote_mode", ["everywhere"])]},
        ).status_code
        == 422
    )
    plain = add_target(client)
    stated = other_target(client, opportunity={"remote_mode": "hybrid"})

    assert plain["opportunity"]["remote_mode"] is None  # absence stays absence
    assert stated["opportunity"]["remote_mode"] == "hybrid"
    assert (
        client.post(
            "/api/targets",
            json={
                "company": {"name": "X Corp"},
                "opportunity": {"title": "T", "remote_mode": "anywhere"},
            },
        ).status_code
        == 422
    )


# --- A hard constraint that cannot be evaluated is never silently ignored --------------


def declare_hard(client: TestClient, constraint_type: str, value: Any) -> None:
    response = client.post(
        "/api/candidate/constraints",
        json={
            "constraint_type": constraint_type,
            "description": "synthetic",
            "value": value,
            "is_hard": True,
        },
    )
    assert response.status_code == 201, response.text


def seed(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/search-profiles/from-preferences", json={"name": "Seeded", **body})
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_an_unevaluable_hard_constraint_keeps_every_target_at_needs_information(
    client: TestClient, with_candidate: None
) -> None:
    declare_hard(client, "salary", {"minimum": 1000})
    declare_hard(client, "contract", {"contract_types": ["apprenticeship"]})
    seed(client)
    with_offer = add_target(client)
    spontaneous = other_target(client, offer=False)

    for target in (with_offer, spontaneous):
        data = qualify(client, target["id"]).json()["qualification"]
        held = next(r for r in data["results"] if r["criterion"]["dimension"] == "constraint")
        assert (held["outcome"], held["code"]) == ("unknown", "not_evaluable")
        assert data["status"] == "needs_information"  # required + unknown, never candidate/excluded
        assert "open_question_required" in reason_codes(data)
        # The evaluable part of the constraints is still enforced next to it.
        assert outcomes(data)["contract_type"] == "satisfied"


def test_an_unevaluable_hard_constraint_never_excludes_by_itself(
    client: TestClient, with_candidate: None
) -> None:
    declare_hard(client, "schedule", {"max_hours": 35})
    declare_hard(client, "availability", None)
    seed(client)
    targets = [
        add_target(client),
        other_target(client, offer=False),
        add_target(client, domain="c.example.invalid", name="C"),
    ]

    report = client.post("/api/qualifications/run").json()

    assert report["by_status"] == {"excluded": 0, "needs_information": 3, "candidate": 0}
    assert {i["status"] for i in report["items"]} == {"needs_information"}
    assert len(targets) == 3


def test_a_known_incompatibility_still_excludes_next_to_a_held_constraint(
    client: TestClient, with_candidate: None
) -> None:
    declare_hard(client, "salary", {"minimum": 1000})
    declare_hard(client, "contract", {"contract_types": ["apprenticeship"]})
    seed(client)
    target = add_target(client, contract_type="full_time")

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "excluded"
    assert {"excluded_by_required", "open_question_required"} <= set(reason_codes(data))


def test_a_soft_unevaluable_constraint_does_not_hold_anything(
    client: TestClient, with_candidate: None
) -> None:
    client.post(
        "/api/candidate/constraints",
        json={
            "constraint_type": "salary",
            "description": "synthetic",
            "value": {"minimum": 1},
            "is_hard": False,
        },
    )
    client.post("/api/candidate/preferences", json={"contract_types": ["apprenticeship"]})
    seed(client)
    target = add_target(client)

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate"
    assert "constraint" not in outcomes(data)


def test_a_held_constraint_is_resolved_only_by_an_explicit_versioned_action(
    client: TestClient, with_candidate: None
) -> None:
    declare_hard(client, "salary", {"minimum": 1000})
    declare_hard(client, "contract", {"contract_types": ["apprenticeship"]})
    profile = seed(client)
    held = next(c for c in profile["criteria"] if c["dimension"] == "constraint")
    target = add_target(client)
    first = qualify(client, target["id"]).json()["qualification"]
    assert first["status"] == "needs_information"

    # The human decides: the criterion becomes a mere preference (a NEW version).
    updated = client.post(
        f"/api/search-profiles/{profile['id']}/criteria",
        json=crit("constraint", ["salary"], "preferred", replaces=held["id"]),
    ).json()

    old = next(c for c in updated["criteria"] if c["id"] == held["id"])
    assert old["active"] is False and old["superseded_by_id"] is not None  # history kept
    assert qualification(client, target["id"])["stale"] is True
    second = qualify(client, target["id"]).json()
    assert second["created"] is True and second["qualification"]["status"] == "candidate"
    assert qualification(client, target["id"])["id"] != first["id"]


def test_deactivating_a_held_constraint_is_explicit_and_audited_by_history(
    client: TestClient, with_candidate: None
) -> None:
    declare_hard(client, "salary", {"minimum": 1000})
    declare_hard(client, "contract", {"contract_types": ["apprenticeship"]})
    profile = seed(client)
    held = next(c for c in profile["criteria"] if c["dimension"] == "constraint")
    target = add_target(client)
    qualify(client, target["id"])

    deactivated = client.delete(
        f"/api/search-profiles/{profile['id']}/criteria/{held['id']}"
    ).json()

    assert deactivated["active"] is False and deactivated["deactivated_at"] is not None
    assert qualify(client, target["id"]).json()["qualification"]["status"] == "candidate"


def test_a_constraint_criterion_can_also_be_created_by_hand(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("constraint", ["visa"], "required")])
    target = add_target(client)

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "needs_information" and outcomes(data) == {"constraint": "unknown"}


# --- No score --------------------------------------------------------------------------

FORBIDDEN_WORDS = (
    "score",
    "percent",
    "pct",
    "rating",
    "weight",
    "points",
    "ratio",
    "rank",
    "grade",
)


def test_a_qualification_contains_no_score_no_percentage_and_no_decimal(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("sector", ["software"], "preferred"),
            crit("keyword", ["python"], "flexible"),
        ],
    )
    target = add_target(client)
    payload = qualify(client, target["id"]).json()
    report = client.post("/api/qualifications/run").json()

    for document in (payload, qualification(client, target["id"]), report):
        keys = all_keys(document)
        assert not [k for k in keys if any(word in k.lower() for word in FORBIDDEN_WORDS)], keys
        assert all(isinstance(n, int) for n in all_numbers(document))  # counters and ids only
        text = str(document)
        assert "%" not in text


# --- Idempotence, immutability, staleness ----------------------------------------------


def test_the_same_inputs_do_not_create_a_new_qualification(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client)

    first = qualify(client, target["id"])
    second = qualify(client, target["id"])
    third = qualify(client, target["id"])

    assert (first.status_code, second.status_code, third.status_code) == (201, 200, 200)
    assert second.json()["created"] is False
    ids = {r.json()["qualification"]["id"] for r in (first, second, third)}
    assert len(ids) == 1
    assert count(db_session, Qualification) == 1
    assert count(db_session, CriterionResult) == 1


def test_a_relevant_data_change_makes_the_qualification_stale_and_a_new_one_computable(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)
    first = qualify(client, target["id"]).json()["qualification"]
    assert first["status"] == "candidate" and first["stale"] is False
    assert qualification(client, target["id"])["stale"] is False

    company = db_session.scalars(select(Company)).one()
    company.sector = "Online gambling services"  # relevant data changed (not via the API)
    db_session.commit()

    stale = qualification(client, target["id"])
    assert stale["id"] == first["id"] and stale["stale"] is True
    assert stale["status"] == "candidate"  # the old one is history: it is not rewritten

    second = qualify(client, target["id"])
    assert second.status_code == 201 and second.json()["created"] is True
    assert second.json()["qualification"]["id"] != first["id"]
    assert count(db_session, Qualification) == 2  # the previous one is still there
    current = qualification(client, target["id"])
    assert current["id"] == second.json()["qualification"]["id"] and current["stale"] is False


def test_an_irrelevant_change_does_not_make_it_stale(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)
    qualify(client, target["id"])

    client.patch(
        f"/api/targets/{target['id']}", json={"status": "shortlisted", "relevance_note": "nice"}
    )

    assert qualification(client, target["id"])["stale"] is False  # workflow status is not an input


def test_changing_the_criteria_makes_qualifications_stale(
    client: TestClient, with_candidate: None
) -> None:
    profile = make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)
    first = qualify(client, target["id"]).json()["qualification"]

    updated = client.post(
        f"/api/search-profiles/{profile['id']}/criteria",
        json=crit("sector", ["fintech"], "required", replaces=profile["criteria"][0]["id"]),
    ).json()

    assert qualification(client, target["id"])["stale"] is True
    second = qualify(client, target["id"]).json()
    assert second["created"] is True and second["qualification"]["status"] == "needs_information"
    # The first qualification still refers to the OLD criterion version (history stays readable).
    old_ids = {r["criterion"]["id"] for r in first["results"]}
    new_ids = {r["criterion"]["id"] for r in second["qualification"]["results"]}
    assert old_ids == {profile["criteria"][0]["id"]} and old_ids.isdisjoint(new_ids)
    assert updated["criteria"][0]["active"] is False


def test_the_previous_qualification_stays_readable_as_history(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)
    first = qualify(client, target["id"]).json()["qualification"]
    db_session.scalars(select(Company)).one().sector = "Online gambling"
    db_session.commit()
    qualify(client, target["id"])

    old = db_session.get(Qualification, first["id"])

    assert old is not None and old.status.value == "candidate"
    assert old.inputs_fingerprint == first["inputs_fingerprint"]


# --- Reasons: codes and references to existing objects ---------------------------------


def test_reasons_reference_only_existing_results_and_render_from_templates(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("country", ["FR"], "required"),
            crit("sector", ["software"], "preferred"),
            crit("keyword", ["python"], "flexible"),
        ],
    )
    target = add_target(client, offer=False, company={"country_code": None})

    data = qualify(client, target["id"]).json()["qualification"]

    result_ids = {r["id"] for r in data["results"]}
    assert data["reasons"], "expected reasons"
    for reason in data["reasons"]:
        if reason["code"] == "no_offer_published":
            assert reason["criterion_result_id"] is None
        else:
            assert reason["criterion_result_id"] in result_ids
        assert reason["origin"] == "deterministic" and reason["text"]
    # In the database every reference is a real foreign key to an existing result.
    stored = db_session.scalars(
        select(QualificationReason).where(QualificationReason.qualification_id == data["id"])
    ).all()
    existing = set(db_session.scalars(select(CriterionResult.id)))
    assert all(r.criterion_result_id in existing for r in stored if r.criterion_result_id)
    assert [r.position for r in stored] == list(range(len(stored)))
    # Nothing stored is free text: a reason is a code and a reference.
    assert {c.name for c in QualificationReason.__table__.columns} == {
        "id",
        "qualification_id",
        "position",
        "code",
        "criterion_result_id",
        "origin",
    }


def test_the_rendered_text_is_deterministic(client: TestClient, with_candidate: None) -> None:
    make_profile(client, [crit("sector", ["software"], "preferred")])
    target = add_target(client)
    qualify(client, target["id"])

    texts = [[r["text"] for r in qualification(client, target["id"])["reasons"]] for _ in range(3)]

    assert texts[0] == texts[1] == texts[2]
    assert texts[0] == ["Preferred sector criterion satisfied (any_of software)"]


def test_results_and_reasons_are_stable_for_identical_inputs(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("sector", ["software"], "preferred"),
            crit("role", ["data"], "preferred"),
        ],
    )
    a = qualify(client, add_target(client)["id"]).json()["qualification"]
    b = qualify(client, add_target(client, domain="b.example.invalid", name="B Corp")["id"]).json()[
        "qualification"
    ]

    def shape(data: dict[str, Any]) -> Any:
        return (
            data["status"],
            [(r["criterion"]["dimension"], r["outcome"], r["code"]) for r in data["results"]],
            reason_codes(data),
            data["counters"],
        )

    assert shape(a) == shape(b)


# --- Profiles and errors ---------------------------------------------------------------


def test_qualification_needs_an_active_profile_with_criteria(
    client: TestClient, with_candidate: None
) -> None:
    target = add_target(client)
    assert qualify(client, target["id"]).status_code == 404  # no profile at all

    empty = make_profile(client, [], name="Empty")
    assert qualify(client, target["id"]).status_code == 422
    assert qualify(client, target["id"], profile_id=empty["id"]).status_code == 422
    assert qualify(client, 999).status_code == 404
    assert qualify(client, target["id"], profile_id=999).status_code == 404
    assert (
        client.get(f"/api/targets/{target['id']}/qualification").status_code == 404
    )  # nothing yet


def test_a_target_can_be_qualified_against_an_explicit_profile(
    client: TestClient, with_candidate: None
) -> None:
    active = make_profile(client, [crit("contract_type", ["apprenticeship"], "required")], name="A")
    other = make_profile(client, [crit("contract_type", ["internship"], "required")], name="B")
    target = add_target(client)

    on_active = qualify(client, target["id"]).json()["qualification"]
    on_other = qualify(client, target["id"], profile_id=other["id"]).json()["qualification"]

    assert on_active["profile_id"] == active["id"] and on_active["status"] == "candidate"
    assert on_other["profile_id"] == other["id"] and on_other["status"] == "excluded"
    assert (
        client.get(
            f"/api/targets/{target['id']}/qualification", params={"profile_id": other["id"]}
        ).json()["status"]
        == "excluded"
    )


def test_an_unqualified_target_has_no_qualification(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(client, [crit("sector", ["software"])])
    target = add_target(client)

    assert client.get(f"/api/targets/{target['id']}/qualification").status_code == 404


# --- Batch run -------------------------------------------------------------------------


def test_the_batch_run_qualifies_everything_once_and_skips_dismissed_targets(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    good = add_target(client)
    bad = other_target(client, contract_type="full_time")
    dismissed = add_target(client, domain="c.example.invalid", name="C Corp")
    client.patch(f"/api/targets/{dismissed['id']}", json={"status": "dismissed"})

    first = client.post("/api/qualifications/run").json()

    assert first["targets_processed"] == 2 and first["created"] == 2 and first["unchanged"] == 0
    assert first["by_status"] == {"excluded": 1, "needs_information": 0, "candidate": 1}
    assert {(i["target_id"], i["status"]) for i in first["items"]} == {
        (good["id"], "candidate"),
        (bad["id"], "excluded"),
    }
    second = client.post("/api/qualifications/run").json()
    assert second["created"] == 0 and second["unchanged"] == 2
    assert count(db_session, Qualification) == 2
    assert client.get(f"/api/targets/{dismissed['id']}/qualification").status_code == 404


def test_the_batch_run_only_recomputes_what_changed(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    add_target(client)
    other_target(client)
    client.post("/api/qualifications/run")
    db_session.scalars(
        select(Company).where(Company.name == "Other Corp")
    ).one().sector = "Gambling"
    db_session.commit()

    report = client.post("/api/qualifications/run").json()

    assert (report["created"], report["unchanged"]) == (1, 1)


def test_the_batch_run_is_audited_with_counters_only(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    add_target(client, name="Company SYNTHETIC-MARKER")
    client.post("/api/qualifications/run")

    (event,) = db_session.scalars(select(AuditEvent)).all()

    assert event.event_type is AuditEventType.QUALIFICATION_RUN and event.actor == "api"
    assert event.details == {"rows": 1, "created": 1, "matched": 0}
    assert "SYNTHETIC-MARKER" not in str([event.details, event.subject])


def test_the_batch_run_needs_a_usable_profile(client: TestClient, with_candidate: None) -> None:
    add_target(client)
    assert client.post("/api/qualifications/run").status_code == 404
    make_profile(client, [], name="Empty")
    assert client.post("/api/qualifications/run").status_code == 422


def test_the_batch_run_writes_the_audit_atomically(
    client: TestClient,
    with_candidate: None,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No audit, no run: if the audit cannot be written nothing is qualified."""
    from app.services import audit as audit_module

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    make_profile(client, [crit("sector", ["software"], "required")])
    add_target(client)
    monkeypatch.setattr(audit_module.AuditLog, "record", broken)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post("/api/qualifications/run")

    db_session.expire_all()
    assert count(db_session, Qualification) == 0


# --- Filter on the target list ---------------------------------------------------------


def test_the_target_list_can_be_filtered_by_qualification(
    client: TestClient, with_candidate: None
) -> None:
    make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("country", ["FR"], "required"),
        ],
    )
    candidate = add_target(client)
    excluded = other_target(client, contract_type="full_time")
    open_question = add_target(
        client, domain="c.example.invalid", name="C Corp", company={"country_code": None}
    )
    not_yet = add_target(client, domain="d.example.invalid", name="D Corp")
    for target in (candidate, excluded, open_question):
        qualify(client, target["id"])

    def ids(value: str) -> set[int]:
        return {t["id"] for t in client.get("/api/targets", params={"qualification": value}).json()}

    assert ids("candidate") == {candidate["id"]}
    assert ids("excluded") == {excluded["id"]}
    assert ids("needs_information") == {open_question["id"]}
    assert ids("none") == {not_yet["id"]}
    assert client.get("/api/targets", params={"qualification": "nonsense"}).status_code == 422


def test_the_filter_uses_the_latest_qualification(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)
    qualify(client, target["id"])
    db_session.scalars(select(Company)).one().sector = "Online gambling"
    db_session.commit()
    qualify(client, target["id"])  # new qualification: needs_information

    def ids(value: str) -> set[int]:
        return {t["id"] for t in client.get("/api/targets", params={"qualification": value}).json()}

    assert ids("candidate") == set() and ids("needs_information") == {target["id"]}


def test_the_filter_needs_an_active_profile(client: TestClient, with_candidate: None) -> None:
    add_target(client)

    assert client.get("/api/targets", params={"qualification": "candidate"}).status_code == 422
    assert client.get("/api/targets").status_code == 200  # no filter: unaffected


# --- Security and safety ---------------------------------------------------------------


def test_the_new_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("post", "/api/targets/1/qualify"),
        ("get", "/api/targets/1/qualification"),
        ("post", "/api/qualifications/run"),
        ("get", "/api/targets?qualification=candidate"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_nothing_touches_the_network(
    client: TestClient, with_candidate: None, no_network: None
) -> None:
    make_profile(client, [crit("sector", ["software"], "required")])
    target = add_target(client)

    assert qualify(client, target["id"]).status_code == 201
    assert client.post("/api/qualifications/run").status_code == 200
    assert client.get("/api/targets", params={"qualification": "candidate"}).status_code == 200
