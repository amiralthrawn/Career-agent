"""Requirement matches inside the qualification, and the PersonalizationBrief (step 3b).

Scenario used below (all synthetic). The offer says: Python required, SQL a plus, Power BI a plus,
"2 years of experience". The Candidate Brain holds: Skill Python (known), Skill SQL (uncertain),
a Project that mentions Power BI and Python (known) but NO Skill Power BI, and one dated
experience of 2 years and 5 months (known).
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.models import (
    Experience,
    Project,
    Qualification,
    RequirementMatch,
    RequirementMatchFact,
    Skill,
    TargetRequirement,
)
from app.models.enums import InformationState, MatchStatus
from tests.qualification_factory import add_target, crit, make_profile, qualification, qualify
from tests.requirements_factory import (
    active_profile,
    add_experience,
    add_project,
    add_skill,
    brief,
    by_key,
    extract,
    give_state,
    manual_body,
    offer_target,
    requirements,
    scenario,
    spontaneous_target,
)

FORBIDDEN_KEYS = ("score", "percent", "ratio", "weight", "probab", "rating", "coverage_rate")


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in all_keys(item)}
    return set()


def matches(client: TestClient, target_id: int) -> dict[str, dict[str, Any]]:
    data = qualification(client, target_id)
    return {m["requirement"]["key"]: m for m in data["requirement_matches"]}


# --- Matches inside the qualification ---------------------------------------------------------


def test_the_qualification_holds_one_match_per_requirement(client: TestClient) -> None:
    target = scenario(client)

    found = matches(client, target["id"])

    assert {key: (m["status"], m["note"]) for key, m in found.items()} == {
        "python": ("covered", "skill_established"),
        "sql": ("weak", "skill_unconfirmed"),
        "power_bi": ("gap", "mentioned_by_project_only"),
        "experience_years:2": ("covered", "experience_established"),
    }
    assert all(m["text"] for m in found.values())  # rendered from a fixed template


def test_raw_counters_are_correct_and_never_combined(client: TestClient) -> None:
    target = scenario(client)

    data = qualification(client, target["id"])

    assert (
        data["requirements_total"],
        data["requirements_covered"],
        data["requirements_weak"],
        data["requirements_gap"],
        data["requirements_unmeasurable"],
    ) == (4, 2, 1, 1, 0)
    assert not {key for key in all_keys(data) if any(bad in key for bad in FORBIDDEN_KEYS)}


def test_experience_dates_that_only_bound_the_duration_are_unmeasurable(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client, "4 years of experience.")
    extract(client, target["id"])
    add_experience(client, "Analyst", "2020", "2024", "known")  # 3 to 5 years, we cannot say

    qualify(client, target["id"])

    data = qualification(client, target["id"])
    assert data["requirements_unmeasurable"] == 1 and data["requirements_covered"] == 0
    assert data["requirement_matches"][0]["note"] == "experience_dates_insufficient"


def test_a_skill_gap_never_changes_the_qualification_status(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client, "Docker is required. Kubernetes is required.")
    plain = qualify(client, target["id"]).json()["qualification"]
    extract(client, target["id"])

    enriched = qualify(client, target["id"]).json()["qualification"]

    assert plain["status"] == enriched["status"] == "candidate"
    assert (enriched["requirements_total"], enriched["requirements_gap"]) == (2, 2)
    assert plain["requirements_total"] == 0
    assert plain["counters"] == enriched["counters"]  # the 3a counters are untouched
    assert plain["id"] != enriched["id"]  # the requirements were new inputs


def test_matching_never_excludes_a_target_kept_by_3a(client: TestClient) -> None:
    make_profile(client, [crit("contract_type", ["apprenticeship"], "required")])
    target = add_target(client, opportunity={"description_text": "Kubernetes is required."})
    extract(client, target["id"])

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["status"] == "candidate" and data["requirements_gap"] == 1


def test_a_spontaneous_target_has_zero_requirements(client: TestClient) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    extract(client, target["id"])

    data = qualify(client, target["id"]).json()["qualification"]

    assert data["requirements_total"] == 0 and data["requirement_matches"] == []
    assert data["requirements_covered"] == data["requirements_weak"] == 0
    assert data["requirements_gap"] == data["requirements_unmeasurable"] == 0


def test_a_manual_requirement_is_matched_like_any_other(client: TestClient) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    add_skill(client, "Docker", "verified")
    client.post(f"/api/targets/{target['id']}/requirements", json=manual_body())

    qualify(client, target["id"])

    (match,) = qualification(client, target["id"])["requirement_matches"]
    assert (match["requirement"]["origin"], match["status"]) == ("manual", "covered")


def test_match_facts_are_references_with_their_evidence_state(client: TestClient) -> None:
    target = scenario(client)

    found = matches(client, target["id"])

    python = {(f["type"], f["name"], f["role"], f["state"]) for f in found["python"]["facts"]}
    assert python == {
        ("skill", "Python", "establishes", "known"),
        ("project", "Dashboard", "supports", "known"),
    }
    assert [(f["type"], f["state"]) for f in found["sql"]["facts"]] == [("skill", "uncertain")]
    assert [(f["type"], f["role"]) for f in found["power_bi"]["facts"]] == [("project", "mentions")]


# --- Fingerprint, staleness, immutability -----------------------------------------------------


def test_qualifying_again_with_the_same_inputs_recomputes_nothing(client: TestClient) -> None:
    target = scenario(client)

    again = qualify(client, target["id"])

    assert again.status_code == 200 and again.json()["created"] is False


def test_a_change_of_the_brain_makes_the_qualification_stale_and_a_new_one_keeps_the_old(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    before = qualification(client, target["id"])
    sql = db_session.scalars(select(Skill).where(Skill.name == "SQL")).one()
    assert before["stale"] is False

    give_state(client, "skill", sql.id, "verified")  # the Brain changed

    assert qualification(client, target["id"])["stale"] is True
    renewed = qualify(client, target["id"])
    assert renewed.status_code == 201
    after = renewed.json()["qualification"]
    assert (after["requirements_covered"], after["requirements_weak"]) == (3, 0)
    assert after["id"] != before["id"] and after["stale"] is False
    # the old qualification still says what it said
    old = db_session.scalars(
        select(RequirementMatch).where(RequirementMatch.qualification_id == before["id"])
    ).all()
    assert sorted(m.status.value for m in old) == ["covered", "covered", "gap", "weak"]


def test_a_new_or_changed_requirement_makes_the_qualification_stale(client: TestClient) -> None:
    target = scenario(client)

    client.post(f"/api/targets/{target['id']}/requirements", json=manual_body())

    assert qualification(client, target["id"])["stale"] is True


def test_a_change_that_does_not_matter_does_not_make_it_stale(client: TestClient) -> None:
    active_profile(client)
    target = spontaneous_target(client)  # no requirement: the Brain is not an input
    qualify(client, target["id"])

    add_skill(client, "Python", "known")

    assert qualification(client, target["id"])["stale"] is False


def test_batch_qualification_records_matches_too(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client)
    extract(client, target["id"])
    add_skill(client, "Python", "known")

    report = client.post("/api/qualifications/run").json()

    assert report["created"] == 1
    assert qualification(client, target["id"])["requirements_covered"] == 1


def test_matches_are_immutable_in_the_orm_and_in_the_database(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)
    match = db_session.scalars(select(RequirementMatch)).first()
    fact = db_session.scalars(select(RequirementMatchFact)).first()
    assert match is not None and fact is not None
    assert qualification(client, target["id"])

    match.status = MatchStatus.GAP
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()
    db_session.rollback()
    fact.state = InformationState.VERIFIED
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()
    db_session.rollback()

    for table in ("requirement_matches", "requirement_match_facts"):
        with pytest.raises(DatabaseError, match="immutable"):
            db_session.execute(text(f"UPDATE {table} SET id = id"))
        db_session.rollback()


def test_a_match_fact_references_exactly_one_brain_fact(db_session: Session) -> None:
    with pytest.raises(DatabaseError):
        db_session.execute(
            text(
                "INSERT INTO requirement_match_facts (match_id, role, state) "
                "VALUES (1, 'supports', 'known')"
            )
        )
    db_session.rollback()


def test_deleting_a_target_removes_its_requirements_and_matches(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)

    db_session.execute(text("PRAGMA foreign_keys = ON"))
    db_session.execute(text("DELETE FROM qualifications"))
    db_session.execute(text("DELETE FROM targets WHERE id = :id"), {"id": target["id"]})
    db_session.commit()

    assert db_session.scalars(select(RequirementMatch)).all() == []
    assert db_session.scalars(select(RequirementMatchFact)).all() == []
    assert db_session.scalars(select(TargetRequirement)).all() == []


# --- PersonalizationBrief ---------------------------------------------------------------------


def test_the_brief_describes_the_target_without_inventing_anything(client: TestClient) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    assert data["target"]["mode"] == "offer" and data["target"]["offer"]["title"]
    assert data["target"]["company"]["name"] == "Fixture Corp"
    assert data["target"]["contract_type"] == "apprenticeship"
    assert data["target"]["source"]["kind"] == "manual"
    assert (
        data["qualification"]["status"] == "candidate" and data["qualification"]["stale"] is False
    )
    assert data["company_context"]["sector"] == "Synthetic software"
    assert data["company_context"]["offer_location"] == "Faketown"


def test_strengths_hold_only_covered_requirements_with_their_facts(client: TestClient) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    assert [s["requirement"]["key"] for s in data["strengths"]] == [
        "python",
        "experience_years:2",
    ]
    python = data["strengths"][0]
    assert python["requirement"]["excerpt"] == "Python is required."
    assert {(f["type"], f["name"], f["state"]) for f in python["facts"]} == {
        ("skill", "Python", "known"),
        ("project", "Dashboard", "known"),
    }


def test_do_not_claim_lists_gaps_weak_and_unmeasurable_requirements(client: TestClient) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    assert {(d["requirement"]["key"], d["status"]) for d in data["do_not_claim"]} == {
        ("sql", "weak"),
        ("power_bi", "gap"),
    }
    covered = {s["requirement"]["id"] for s in data["strengths"]}
    assert covered.isdisjoint({d["requirement"]["id"] for d in data["do_not_claim"]})


def test_do_not_claim_includes_an_unmeasurable_duration(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client, "4 years of experience.")
    extract(client, target["id"])
    add_experience(client, "Analyst", "2020", "2024", "known")
    qualify(client, target["id"])

    data = brief(client, target["id"])

    assert [(d["requirement"]["key"], d["status"]) for d in data["do_not_claim"]] == [
        ("experience_years:4", "unmeasurable")
    ]
    assert [q["question"] for q in data["open_questions"]] == ["provide_experience_dates"]


def test_open_questions_hold_what_only_the_human_can_confirm(client: TestClient) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    questions = {q["requirement"]["key"]: q for q in data["open_questions"]}
    assert set(questions) == {"sql", "power_bi"}
    assert questions["sql"]["question"] == "confirm_skill_evidence"
    assert questions["power_bi"]["question"] == "confirm_project_skill"
    assert [f["name"] for f in questions["power_bi"]["facts"]] == ["Dashboard"]
    assert "Power BI" in questions["power_bi"]["text"]


def test_emphasis_candidates_are_ranked_by_covered_requirements_then_a_fixed_order(
    client: TestClient,
) -> None:
    active_profile(client)
    target = offer_target(client, "Python is required. SQL is required.")
    extract(client, target["id"])
    skill_python = add_skill(client, "Python", "known")
    skill_sql = add_skill(client, "SQL", "verified")
    proj = add_project(client, "Warehouse", "Python jobs loading a SQL warehouse", "known")
    add_project(client, "Unrelated", "Python only, but the evidence is unknown", "unknown")
    qualify(client, target["id"])

    data = brief(client, target["id"])

    assert [
        (c["rank"], c["fact"]["type"], c["fact"]["id"], c["covered_count"])
        for c in data["emphasis_candidates"]
    ] == [
        (1, "project", proj["id"], 2),  # supports both covered requirements
        (2, "skill", skill_python["id"], 1),
        (3, "skill", skill_sql["id"], 1),
    ]
    # a fact the Brain cannot back (unknown state) is never put forward
    assert "Unrelated" not in json.dumps(data["emphasis_candidates"])


def test_the_brief_is_deterministic(client: TestClient) -> None:
    target = scenario(client)

    first = brief(client, target["id"])
    second = brief(client, target["id"])
    qualify(client, target["id"])  # same inputs: nothing new
    third = brief(client, target["id"])

    assert first == second == third
    assert json.dumps(first, sort_keys=True) == json.dumps(third, sort_keys=True)


def test_every_reference_of_the_brief_points_to_an_existing_object(
    client: TestClient, db_session: Session
) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    requirement_ids = {r for r in db_session.scalars(select(TargetRequirement.id))}
    fact_ids = {
        "skill": set(db_session.scalars(select(Skill.id))),
        "project": set(db_session.scalars(select(Project.id))),
        "experience": set(db_session.scalars(select(Experience.id))),
    }
    assert data["qualification"]["id"] in set(db_session.scalars(select(Qualification.id)))
    refs = [s["requirement"] for s in data["strengths"]]
    refs += [d["requirement"] for d in data["do_not_claim"]]
    refs += [q["requirement"] for q in data["open_questions"]]
    assert refs and {r["id"] for r in refs} <= requirement_ids
    facts = [f for s in data["strengths"] for f in s["facts"]]
    facts += [f for d in data["do_not_claim"] for f in d["facts"]]
    facts += [f for q in data["open_questions"] for f in q["facts"]]
    facts += [c["fact"] for c in data["emphasis_candidates"]]
    assert facts and all(f["id"] in fact_ids[f["type"]] for f in facts)


def test_the_brief_holds_no_score_no_timestamp_and_no_offer_text(client: TestClient) -> None:
    target = scenario(client)

    data = brief(client, target["id"])

    keys = all_keys(data)
    assert not {key for key in keys if any(bad in key for bad in FORBIDDEN_KEYS)}
    assert not {key for key in keys if key.endswith("_at")}
    assert "description_text" not in keys
    assert set(data) == {
        "target",
        "qualification",
        "strengths",
        "do_not_claim",
        "open_questions",
        "emphasis_candidates",
        "company_context",
    }


def test_the_brief_of_a_spontaneous_target_is_valid_and_empty_of_requirements(
    client: TestClient,
) -> None:
    active_profile(client)
    target = spontaneous_target(client)
    qualify(client, target["id"])

    data = brief(client, target["id"])

    assert data["target"]["mode"] == "spontaneous" and data["target"]["offer"] is None
    assert data["strengths"] == data["do_not_claim"] == data["open_questions"] == []
    assert data["emphasis_candidates"] == []
    assert data["company_context"]["offer_location"] is None


def test_a_brief_needs_a_qualification_and_says_when_it_is_stale(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client)
    extract(client, target["id"])

    assert client.get(f"/api/targets/{target['id']}/personalization-brief").status_code == 404
    assert client.get("/api/targets/999/personalization-brief").status_code == 404

    qualify(client, target["id"])
    add_skill(client, "Python", "known")  # the Brain moved on since the qualification

    assert brief(client, target["id"])["qualification"]["stale"] is True


def test_requirements_extraction_and_briefs_never_touch_the_network(
    client: TestClient, no_network: None
) -> None:
    target = scenario(client)

    brief(client, target["id"])
    assert requirements(client, target["id"])
    assert by_key(requirements(client, target["id"]))["python"]["origin"] == "offer_text"


# --- Explicit coverage (SQL / PostgreSQL), end to end -----------------------------------------


def test_a_postgresql_skill_covers_a_sql_requirement_but_pandas_does_not_cover_python(
    client: TestClient,
) -> None:
    active_profile(client)
    target = offer_target(client, "SQL is required. Python is required.")
    extract(client, target["id"])
    postgres = add_skill(client, "PostgreSQL", "verified")
    add_skill(client, "pandas", "verified")
    qualify(client, target["id"])

    found = matches(client, target["id"])

    assert (found["sql"]["status"], found["sql"]["note"]) == ("covered", "skill_implied")
    assert [(f["type"], f["id"], f["role"], f["state"]) for f in found["sql"]["facts"]] == [
        ("skill", postgres["id"], "establishes", "verified")
    ]
    assert (found["python"]["status"], found["python"]["note"]) == ("gap", "no_skill_in_brain")
    data = brief(client, target["id"])
    assert [s["requirement"]["key"] for s in data["strengths"]] == ["sql"]
    assert [d["requirement"]["key"] for d in data["do_not_claim"]] == ["python"]


def test_a_sql_skill_does_not_cover_a_postgresql_requirement(client: TestClient) -> None:
    active_profile(client)
    target = offer_target(client, "PostgreSQL is required.")
    extract(client, target["id"])
    add_skill(client, "SQL", "verified")

    qualify(client, target["id"])

    assert matches(client, target["id"])["postgresql"]["status"] == "gap"


# --- The stale contract of the brief ----------------------------------------------------------


def test_a_stale_brief_is_returned_not_refused_and_the_contract_is_documented(
    client: TestClient,
) -> None:
    from app.core.config import PROJECT_ROOT

    target = scenario(client)
    add_skill(client, "Docker", "known")  # the Brain moved on since the qualification

    response = client.get(f"/api/targets/{target['id']}/personalization-brief")

    assert response.status_code == 200 and response.json()["qualification"]["stale"] is True
    # the field explains the obligation of the future personalisation module...
    schema = client.get("/openapi.json").json()["components"]["schemas"]["BriefQualification"]
    assert "MUST NOT generate" in schema["properties"]["stale"]["description"]
    # ... and so does the documentation
    doc = (PROJECT_ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    assert "MUST check `qualification.stale` before generating any" in " ".join(doc.split())
    # re-qualifying makes the brief current again
    qualify(client, target["id"])
    assert brief(client, target["id"])["qualification"]["stale"] is False
