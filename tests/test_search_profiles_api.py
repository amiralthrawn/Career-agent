"""Search profiles and criteria: versioning, immutability, explicit seeding from preferences."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.qualification_factory import crit, make_profile

BASE = "/api/search-profiles"


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def by_ref(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["origin_ref"]: c for c in profile["criteria"] if c["origin_ref"]}


# --- Profiles --------------------------------------------------------------------------


def test_a_profile_is_created_with_its_criteria(client: TestClient, with_candidate: None) -> None:
    profile = make_profile(
        client,
        [
            crit("contract_type", ["apprenticeship"], "required"),
            crit("role", ["data"], "preferred", note="main aim"),
            crit("keyword", ["python", "sql"], "flexible"),
        ],
    )

    assert profile["is_active"] is True and profile["origin"] == "manual"
    assert [(c["dimension"], c["level"], c["active"]) for c in profile["criteria"]] == [
        ("contract_type", "required", True),
        ("role", "preferred", True),
        ("keyword", "flexible", True),
    ]
    assert profile["criteria"][1]["note"] == "main aim"
    assert client.get(BASE).json()[0]["id"] == profile["id"]


def test_only_one_profile_is_active(client: TestClient, with_candidate: None) -> None:
    first = make_profile(client, [], name="First")
    second = make_profile(client, [], name="Second")  # not activated by default
    third = make_profile(client, [], name="Third", is_active=True)

    states = {p["name"]: p["is_active"] for p in client.get(BASE).json()}

    assert first["is_active"] is True and second["is_active"] is False
    assert states == {"First": False, "Second": False, "Third": True}
    assert third["is_active"] is True


def test_profile_names_are_unique(client: TestClient, with_candidate: None) -> None:
    make_profile(client, [], name="Same")

    assert client.post(BASE, json={"name": "Same"}).status_code == 409


def test_profiles_need_a_candidate(client: TestClient) -> None:
    assert client.post(BASE, json={"name": "X"}).status_code == 404
    assert client.get(BASE).status_code == 404


# --- Criterion validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "criterion",
    [
        crit("nonsense", ["x"]),
        crit("sector", ["x"], level="mandatory"),
        crit("sector", ["x"], operator="all_of"),
        crit("sector", []),
        crit("sector", ["   "]),
        crit("sector", ["!!!"]),  # nothing usable once normalised
        crit("sector", ["x"] * 51),
        crit("sector", ["x" * 101]),
        crit("contract_type", ["permanent-forever"]),
        crit("country", ["FRA"]),
        crit("country", ["1F"]),
        crit("sector", ["x"], note=""),
    ],
)
def test_invalid_criteria_are_rejected(
    client: TestClient, with_candidate: None, criterion: dict[str, Any]
) -> None:
    assert client.post(BASE, json={"name": "P", "criteria": [criterion]}).status_code == 422


def test_criterion_values_are_normalised_and_deduplicated(
    client: TestClient, with_candidate: None
) -> None:
    profile = make_profile(
        client,
        [
            crit("contract_type", ["Apprenticeship", " apprenticeship ", "INTERNSHIP"]),
            crit("country", ["fr", "FR", "de"]),
            crit("sector", [" Software ", "Software"], "preferred"),
        ],
    )

    values = {c["dimension"]: c["values"] for c in profile["criteria"]}
    assert values["contract_type"] == ["apprenticeship", "internship"]
    assert values["country"] == ["FR", "DE"]
    assert values["sector"] == ["Software"]


# --- Immutability: versions, not edits -------------------------------------------------


def test_replacing_a_criterion_creates_a_new_version_and_keeps_the_history(
    client: TestClient, with_candidate: None
) -> None:
    profile = make_profile(client, [crit("sector", ["software"], "preferred")])
    old = profile["criteria"][0]

    updated = client.post(
        f"{BASE}/{profile['id']}/criteria",
        json=crit("sector", ["software", "fintech"], "required", replaces=old["id"]),
    ).json()

    history = {c["id"]: c for c in updated["criteria"]}
    new = next(c for c in updated["criteria"] if c["id"] != old["id"])
    assert history[old["id"]]["active"] is False
    assert history[old["id"]]["superseded_by_id"] == new["id"]
    assert history[old["id"]]["deactivated_at"] is not None
    # The old version keeps exactly its original content.
    assert (history[old["id"]]["values"], history[old["id"]]["level"]) == (
        ["software"],
        "preferred",
    )
    assert (new["values"], new["level"], new["active"]) == (
        ["software", "fintech"],
        "required",
        True,
    )


def test_a_criterion_can_only_be_replaced_once_and_only_in_its_profile(
    client: TestClient, with_candidate: None
) -> None:
    profile = make_profile(client, [crit("sector", ["a"])])
    other = make_profile(client, [], name="Other")
    old = profile["criteria"][0]["id"]
    url = f"{BASE}/{profile['id']}/criteria"
    assert client.post(url, json=crit("sector", ["b"], replaces=old)).status_code == 201

    assert client.post(url, json=crit("sector", ["c"], replaces=old)).status_code == 422
    assert client.post(url, json=crit("sector", ["c"], replaces=999)).status_code == 404
    assert (
        client.post(
            f"{BASE}/{other['id']}/criteria", json=crit("sector", ["c"], replaces=old)
        ).status_code
        == 404
    )


def test_adding_a_criterion_without_replacing_keeps_the_others(
    client: TestClient, with_candidate: None
) -> None:
    profile = make_profile(client, [crit("sector", ["a"])])

    updated = client.post(
        f"{BASE}/{profile['id']}/criteria", json=crit("role", ["b"], "preferred")
    ).json()

    assert [c["active"] for c in updated["criteria"]] == [True, True]


def test_deleting_a_criterion_only_deactivates_it(client: TestClient, with_candidate: None) -> None:
    profile = make_profile(client, [crit("sector", ["a"])])
    criterion_id = profile["criteria"][0]["id"]
    url = f"{BASE}/{profile['id']}/criteria/{criterion_id}"

    deactivated = client.delete(url)
    again = client.delete(url)

    assert deactivated.status_code == 200 and deactivated.json()["active"] is False
    assert deactivated.json()["deactivated_at"] is not None
    assert again.status_code == 200
    # Same instant (SQLite drops the timezone marker on reload, PostgreSQL keeps it).
    assert again.json()["deactivated_at"].rstrip("Z") == deactivated.json()[
        "deactivated_at"
    ].rstrip("Z")
    remaining = client.get(BASE).json()[0]["criteria"]
    assert [c["id"] for c in remaining] == [criterion_id]  # still readable, no longer active
    assert client.delete(f"{BASE}/{profile['id']}/criteria/999").status_code == 404
    assert client.delete(f"{BASE}/999/criteria/{criterion_id}").status_code == 404


# --- Seeding from preferences and constraints ------------------------------------------


def declare_preferences(client: TestClient, **fields: Any) -> None:
    body = {
        "target_roles": ["Data analyst", "Data engineer"],
        "contract_types": ["apprenticeship"],
        "preferred_locations": ["Faketown"],
        "target_sectors": ["software"],
        "target_domains": ["Data/IA"],
        "target_companies": ["Fixture Corp"],
        **fields,
    }
    assert client.post("/api/candidate/preferences", json=body).status_code == 201


def declare_constraint(
    client: TestClient, constraint_type: str, value: Any, hard: bool = True
) -> int:
    response = client.post(
        "/api/candidate/constraints",
        json={
            "constraint_type": constraint_type,
            "description": "synthetic constraint",
            "value": value,
            "is_hard": hard,
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_seeding_maps_preferences_with_explicit_levels(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(client)

    response = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"})

    assert response.status_code == 201
    profile = response.json()
    assert profile["origin"] == "from_preferences" and profile["is_active"] is True
    refs = by_ref(profile)
    expected = {
        "preference:target_roles": ("role", "preferred", ["Data analyst", "Data engineer"]),
        "preference:contract_types": ("contract_type", "required", ["apprenticeship"]),
        "preference:preferred_locations": ("location", "preferred", ["Faketown"]),
        "preference:target_sectors": ("sector", "preferred", ["software"]),
        "preference:target_companies": ("company", "preferred", ["Fixture Corp"]),
        "preference:target_domains": ("keyword", "flexible", ["Data", "IA"]),  # a slash splits
    }
    assert {ref: (c["dimension"], c["level"], c["values"]) for ref, c in refs.items()} == expected
    assert all(c["origin"] == "preference" and c["operator"] == "any_of" for c in refs.values())
    assert profile["unmapped"] == []


def test_seeding_maps_constraints_by_hardness(client: TestClient, with_candidate: None) -> None:
    hard = declare_constraint(
        client, "geographic", {"countries": ["fr"], "excluded_locations": ["Lyon"]}
    )
    soft = declare_constraint(
        client, "contract", {"excluded_contract_types": ["full_time"]}, hard=False
    )

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    refs = {(c["origin_ref"], c["dimension"], c["operator"]): c for c in profile["criteria"]}
    assert refs[(f"constraint:{hard}", "country", "any_of")]["level"] == "required"
    assert refs[(f"constraint:{hard}", "country", "any_of")]["values"] == ["FR"]
    assert refs[(f"constraint:{hard}", "location", "none_of")]["level"] == "required"
    assert refs[(f"constraint:{soft}", "contract_type", "none_of")]["level"] == "preferred"
    assert all(c["origin"] == "constraint" for c in profile["criteria"])


def criteria_of(profile: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    return [c for c in profile["criteria"] if c["origin_ref"] == ref]


def test_soft_preferences_without_data_are_reported_never_silently_dropped(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(
        client, company_size_preferences=["startup"], minimum_salary=1000, salary_currency="EUR"
    )

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    assert {(u["source"], u["code"]) for u in profile["unmapped"]} == {
        ("preference:company_size_preferences", "no_evaluable_data"),
        ("preference:minimum_salary", "no_evaluable_data"),
    }
    assert client.get(BASE).json()[0]["unmapped"] == profile["unmapped"]  # persisted


@pytest.mark.parametrize(
    ("preference", "expected"),
    [("remote", ["remote"]), ("hybrid", ["hybrid"]), ("onsite", ["onsite"])],
)
def test_a_remote_preference_becomes_a_remote_mode_criterion(
    client: TestClient, with_candidate: None, preference: str, expected: list[str]
) -> None:
    declare_preferences(client, remote_preference=preference)

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    (criterion,) = criteria_of(profile, "preference:remote_preference")
    assert (criterion["dimension"], criterion["level"], criterion["values"]) == (
        "remote_mode",
        "preferred",
        expected,
    )
    assert profile["unmapped"] == []


def test_no_remote_preference_states_no_criterion(client: TestClient, with_candidate: None) -> None:
    declare_preferences(client, remote_preference="no_preference")

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    assert criteria_of(profile, "preference:remote_preference") == [] and profile["unmapped"] == []


def test_the_remote_criterion_level_can_be_overridden(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(client, remote_preference="remote")

    profile = client.post(
        f"{BASE}/from-preferences", json={"name": "Seeded", "levels": {"remote_mode": "required"}}
    ).json()

    assert criteria_of(profile, "preference:remote_preference")[0]["level"] == "required"


# --- A HARD constraint is never silently ignored ---------------------------------------


@pytest.mark.parametrize(
    ("constraint_type", "value"),
    [
        ("salary", {"minimum": 1000}),
        ("availability", {"from": "2026-09"}),
        ("schedule", {"max_hours": 35}),
        ("other", None),
        ("other", {"free": "text"}),
        ("geographic", {"max_distance_km": 30}),  # a geographic key that is not evaluable
    ],
)
def test_a_hard_constraint_that_cannot_be_evaluated_is_held_as_a_required_unknown(
    client: TestClient, with_candidate: None, constraint_type: str, value: Any
) -> None:
    constraint = declare_constraint(client, constraint_type, value, hard=True)

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    (held,) = criteria_of(profile, f"constraint:{constraint}")
    assert (held["dimension"], held["level"], held["operator"]) == (
        "constraint",
        "required",
        "any_of",
    )
    assert held["values"] == [constraint_type] and held["origin"] == "constraint"
    assert held["note"] == "Declared hard constraint that cannot be evaluated yet"
    assert held["active"] is True
    assert {(u["source"], u["code"]) for u in profile["unmapped"]} == {
        (f"constraint:{constraint}", "hard_constraint_unevaluable")
    }  # and it is still reported


def test_a_partially_evaluable_hard_constraint_keeps_its_evaluable_part_and_holds_the_rest(
    client: TestClient, with_candidate: None
) -> None:
    constraint = declare_constraint(
        client, "geographic", {"countries": ["FR"], "max_distance_km": 30}
    )

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    kinds = {(c["dimension"], c["level"]) for c in criteria_of(profile, f"constraint:{constraint}")}
    assert kinds == {("country", "required"), ("constraint", "required")}
    assert {(u["source"], u["code"]) for u in profile["unmapped"]} == {
        (f"constraint:{constraint}", "hard_constraint_partially_held")
    }


def test_a_hard_constraint_with_an_invalid_value_is_held_and_its_valid_part_kept(
    client: TestClient, with_candidate: None
) -> None:
    constraint = declare_constraint(
        client,
        "contract",
        {"contract_types": ["permanent-forever"], "excluded_contract_types": ["internship"]},
    )

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    kinds = {
        (c["dimension"], c["operator"], c["level"])
        for c in criteria_of(profile, f"constraint:{constraint}")
    }
    assert kinds == {("contract_type", "none_of", "required"), ("constraint", "any_of", "required")}
    assert {u["code"] for u in profile["unmapped"]} == {"hard_constraint_unevaluable"}


def test_soft_constraints_that_cannot_be_evaluated_are_only_reported(
    client: TestClient, with_candidate: None
) -> None:
    salary = declare_constraint(client, "salary", {"minimum": 1000}, hard=False)
    partial = declare_constraint(
        client, "geographic", {"countries": ["FR"], "max_distance_km": 30}, hard=False
    )
    bad = declare_constraint(
        client, "contract", {"contract_types": ["permanent-forever"]}, hard=False
    )

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    assert {(u["source"], u["code"]) for u in profile["unmapped"]} == {
        (f"constraint:{salary}", "unsupported_constraint"),
        (f"constraint:{partial}", "partially_mapped"),
        (f"constraint:{bad}", "invalid_value"),
    }
    assert criteria_of(profile, f"constraint:{salary}") == []  # soft: nothing is created
    assert [c["dimension"] for c in criteria_of(profile, f"constraint:{partial}")] == ["country"]
    assert all(c["level"] == "preferred" for c in profile["criteria"])


def test_no_hard_constraint_ever_disappears_from_the_profile(
    client: TestClient, with_candidate: None
) -> None:
    """Every hard constraint leaves at least one REQUIRED criterion, whatever its content."""
    hard_ids = [
        declare_constraint(client, "salary", {"minimum": 1}),
        declare_constraint(client, "geographic", {"countries": ["fr"]}),
        declare_constraint(client, "geographic", {"max_distance_km": 5}),
        declare_constraint(client, "contract", {"contract_types": ["nonsense"]}),
        declare_constraint(client, "contract", {"excluded_contract_types": ["internship"]}),
        declare_constraint(client, "schedule", None),
        declare_constraint(client, "other", {"anything": 1}),
    ]

    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()

    for constraint_id in hard_ids:
        required = [
            c
            for c in criteria_of(profile, f"constraint:{constraint_id}")
            if c["level"] == "required"
        ]
        assert required, f"hard constraint {constraint_id} left no required criterion"


def test_seeding_levels_can_be_overridden_for_preferences_only(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(client)
    hard = declare_constraint(client, "contract", {"contract_types": ["apprenticeship"]})

    profile = client.post(
        f"{BASE}/from-preferences",
        json={"name": "Seeded", "levels": {"contract_type": "preferred", "sector": "required"}},
    ).json()

    refs = by_ref(profile)
    assert refs["preference:contract_types"]["level"] == "preferred"
    assert refs["preference:target_sectors"]["level"] == "required"
    assert refs[f"constraint:{hard}"]["level"] == "required"  # follows is_hard, not the override


def test_seeding_needs_something_to_seed_from(client: TestClient, with_candidate: None) -> None:
    assert client.post(f"{BASE}/from-preferences", json={"name": "Empty"}).status_code == 422


def test_seeding_needs_a_candidate(client: TestClient) -> None:
    assert client.post(f"{BASE}/from-preferences", json={"name": "X"}).status_code == 404


def test_seeded_criteria_are_ordinary_immutable_criteria(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(client)
    profile = client.post(f"{BASE}/from-preferences", json={"name": "Seeded"}).json()
    role = by_ref(profile)["preference:target_roles"]

    updated = client.post(
        f"{BASE}/{profile['id']}/criteria",
        json=crit("role", ["data scientist"], "preferred", replaces=role["id"]),
    ).json()

    new = next(c for c in updated["criteria"] if c["values"] == ["data scientist"])
    assert new["origin"] == "manual"  # a human-made version, distinct from the seeded one
    assert {c["id"]: c["active"] for c in updated["criteria"]}[role["id"]] is False


def test_declared_preferences_are_not_modified_by_seeding(
    client: TestClient, with_candidate: None
) -> None:
    declare_preferences(client)
    before = client.get("/api/candidate/preferences").json()

    client.post(f"{BASE}/from-preferences", json={"name": "Seeded"})

    assert client.get("/api/candidate/preferences").json() == before


def test_profile_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("get", BASE),
        ("post", BASE),
        ("post", f"{BASE}/from-preferences"),
        ("post", f"{BASE}/1/criteria"),
        ("delete", f"{BASE}/1/criteria/1"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)
