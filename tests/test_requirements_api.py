"""Requirements of a target end to end: provenance, exact excerpts, no duplicates, immutability."""

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.models import AuditEvent, Opportunity, TargetRequirement
from app.models.audit import AuditEventType
from tests.qualification_factory import add_target, other_target
from tests.requirements_factory import (
    MANUAL_SOURCE,
    OFFER_TEXT,
    by_key,
    extract,
    manual_body,
    offer_target,
    requirements,
    spontaneous_target,
)


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TargetRequirement)) or 0


# --- Extraction: only when asked, only what the offer says ----------------------------------


def test_extraction_creates_the_requirements_the_offer_states(client: TestClient) -> None:
    target = offer_target(client)

    report = extract(client, target["id"])

    assert report["outcome"] == "extracted"
    found = by_key(report["requirements"])
    assert {key: item["importance"] for key, item in found.items()} == {
        "python": "required",
        "sql": "nice_to_have",
        "power_bi": "nice_to_have",
        "experience_years:2": "unspecified",
    }
    assert found["experience_years:2"]["value"] == "2 years"
    assert found["experience_years:2"]["kind"] == "experience"
    assert (report["created"], report["unchanged"], report["superseded"]) == (4, 0, 0)


def test_every_requirement_keeps_its_provenance_and_the_exact_excerpt(client: TestClient) -> None:
    target = offer_target(client)

    items = extract(client, target["id"])["requirements"]

    assert items
    for item in items:
        assert item["origin"] == "offer_text"
        assert item["source_field"] == "opportunity.description_text"
        assert item["source"]["id"] and item["source"]["kind"]  # the source of the offer itself
        assert (
            item["source_hash"] == hashlib.sha256(OFFER_TEXT.strip().encode()).hexdigest()
        )  # the stored text
        assert item["extractor_version"]
        assert item["excerpt"] and item["excerpt"] in OFFER_TEXT  # exact slice, not a rewrite
    assert by_key(items)["python"]["excerpt"] == "Python is required."


def test_the_source_of_an_extracted_requirement_is_the_source_of_the_offer(
    client: TestClient,
) -> None:
    target = offer_target(client)
    offer_source = client.get(f"/api/targets/{target['id']}").json()["opportunity"]["source"]

    items = extract(client, target["id"])["requirements"]

    assert {item["source"]["id"] for item in items} == {offer_source["id"]}


def test_nothing_is_extracted_unless_asked(client: TestClient, db_session: Session) -> None:
    target = offer_target(client)

    client.get(f"/api/targets/{target['id']}")
    client.get("/api/targets")
    assert requirements(client, target["id"]) == [] and count(db_session) == 0


def test_extracting_twice_creates_no_duplicate(client: TestClient, db_session: Session) -> None:
    target = offer_target(client)
    first = extract(client, target["id"])

    second = extract(client, target["id"])

    assert (second["created"], second["unchanged"], second["superseded"], second["retired"]) == (
        0,
        4,
        0,
        0,
    )
    assert [r["id"] for r in second["requirements"]] == [r["id"] for r in first["requirements"]]
    assert count(db_session) == 4


def test_a_spontaneous_target_has_no_requirement_and_that_is_not_an_error(
    client: TestClient, db_session: Session
) -> None:
    target = spontaneous_target(client)

    report = extract(client, target["id"])

    assert (report["outcome"], report["found"], report["requirements"]) == ("no_offer", 0, [])
    assert count(db_session) == 0


def test_an_offer_without_text_gives_nothing(client: TestClient) -> None:
    target = add_target(client, opportunity={"description_text": None})

    assert extract(client, target["id"])["outcome"] == "no_description"


def test_an_offer_that_states_nothing_recognisable_gives_no_requirement(client: TestClient) -> None:
    target = offer_target(client, "We are a friendly team in a lovely town.")

    assert extract(client, target["id"])["requirements"] == []


def test_a_changed_offer_supersedes_and_retires_without_deleting(
    client: TestClient, db_session: Session
) -> None:
    target = offer_target(client)
    old = by_key(extract(client, target["id"])["requirements"])
    offer = db_session.scalars(select(Opportunity)).one()
    offer.description_text = "Python is a plus.\nDocker is required."
    db_session.commit()

    report = extract(client, target["id"])

    assert (report["created"], report["superseded"], report["retired"]) == (1, 1, 3)
    active = by_key(report["requirements"])
    assert set(active) == {"python", "docker"}
    assert active["python"]["importance"] == "nice_to_have"
    history = by_key(requirements(client, target["id"], include_inactive=True))
    assert count(db_session) == 6  # nothing was deleted
    old_python = next(
        r
        for r in requirements(client, target["id"], include_inactive=True)
        if r["id"] == old["python"]["id"]
    )
    assert (
        old_python["active"] is False and old_python["superseded_by_id"] == active["python"]["id"]
    )
    assert old_python["importance"] == "required"  # the old version is intact
    assert history


def test_extraction_is_audited_with_counters_only(client: TestClient, db_session: Session) -> None:
    target = offer_target(client)

    extract(client, target["id"])

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.REQUIREMENTS_EXTRACTED)
    ).one()
    assert event.subject == f"target:{target['id']}"
    assert event.details == {"rows": 4, "created": 4, "matched": 0}
    assert "Python" not in str(event.details) and "Python" not in str(event.subject)


def test_unknown_targets_are_404(client: TestClient) -> None:
    assert client.get("/api/targets/999/requirements").status_code == 404
    assert client.post("/api/targets/999/requirements/extract").status_code == 404
    assert client.post("/api/targets/999/requirements", json=manual_body()).status_code == 404


# --- Manual requirements ----------------------------------------------------------------------


def test_a_manual_requirement_keeps_its_excerpt_and_says_where_it_comes_from(
    client: TestClient,
) -> None:
    target = spontaneous_target(client)

    response = client.post(f"/api/targets/{target['id']}/requirements", json=manual_body())

    assert response.status_code == 201
    item = response.json()["requirement"]
    assert item["origin"] == "manual" and item["source_field"] == "manual"
    assert item["excerpt"] == "The recruiter said Docker is mandatory."
    assert item["source"]["reference"] == MANUAL_SOURCE["reference"]
    assert (item["key"], item["label"], item["importance"]) == ("docker", "Docker", "required")
    assert item["extractor_version"] is None
    # a spontaneous target may carry manual requirements even though it has no offer
    assert [r["key"] for r in requirements(client, target["id"])] == ["docker"]


def test_a_requirement_without_source_or_excerpt_is_refused(
    client: TestClient, db_session: Session
) -> None:
    target = offer_target(client)
    url = f"/api/targets/{target['id']}/requirements"

    assert client.post(url, json=manual_body(source={"kind": "manual"})).status_code == 422
    assert (
        client.post(url, json={k: v for k, v in manual_body().items() if k != "source"}).status_code
        == 422
    )
    assert client.post(url, json=manual_body(excerpt="")).status_code == 422
    assert client.post(url, json=manual_body(excerpt="   ")).status_code == 422
    assert client.post(url, json=manual_body(label="")).status_code == 422
    assert count(db_session) == 0


def test_the_origin_cannot_be_chosen_by_the_caller(client: TestClient) -> None:
    target = spontaneous_target(client)

    response = client.post(
        f"/api/targets/{target['id']}/requirements",
        json=manual_body(origin="company_signal", extractor_version="extract-1"),
    )

    assert response.json()["requirement"]["origin"] == "manual"  # `company_signal` is reserved


def test_a_manual_experience_needs_its_duration_as_written(client: TestClient) -> None:
    target = spontaneous_target(client)
    url = f"/api/targets/{target['id']}/requirements"
    body = manual_body(kind="experience", label="Some experience", excerpt="3 ans d'expérience")

    assert client.post(url, json=body).status_code == 422
    created = client.post(url, json={**body, "value": "3 ans"})
    assert created.status_code == 201 and created.json()["requirement"]["key"] == (
        "experience_years:3"
    )
    assert client.post(url, json=manual_body(value="3 ans")).status_code == 422  # skill + duration
    assert (
        client.post(url, json={**body, "value": "many years"}).status_code == 422
    )  # unreadable duration


def test_a_manual_requirement_is_idempotent_and_a_change_creates_a_new_version(
    client: TestClient, db_session: Session
) -> None:
    target = spontaneous_target(client)
    url = f"/api/targets/{target['id']}/requirements"
    first = client.post(url, json=manual_body()).json()["requirement"]

    same = client.post(url, json=manual_body())
    changed = client.post(url, json=manual_body(importance="nice_to_have"))

    assert same.status_code == 200 and same.json()["created"] is False
    assert same.json()["requirement"]["id"] == first["id"]
    assert changed.status_code == 201
    active = requirements(client, target["id"])
    assert [(r["id"], r["importance"]) for r in active] == [
        (changed.json()["requirement"]["id"], "nice_to_have")
    ]
    everything = requirements(client, target["id"], include_inactive=True)
    old = next(r for r in everything if r["id"] == first["id"])
    assert old["active"] is False and old["importance"] == "required"
    assert old["superseded_by_id"] == active[0]["id"] and count(db_session) == 2


def test_a_manual_requirement_supersedes_an_extracted_one_and_is_never_overwritten(
    client: TestClient,
) -> None:
    target = offer_target(client)
    extracted = by_key(extract(client, target["id"])["requirements"])["sql"]
    assert extracted["importance"] == "nice_to_have"

    manual = client.post(
        f"/api/targets/{target['id']}/requirements",
        json=manual_body(label="SQL", importance="required", excerpt="Told on the phone: SQL"),
    ).json()["requirement"]
    report = extract(client, target["id"])

    assert manual["key"] == "sql" and manual["origin"] == "manual"
    assert (report["kept_manual"], report["superseded"]) == (1, 0)
    current = by_key(report["requirements"])["sql"]
    assert current["id"] == manual["id"] and current["importance"] == "required"
    old = next(
        r
        for r in requirements(client, target["id"], include_inactive=True)
        if r["id"] == extracted["id"]
    )
    assert old["active"] is False and old["superseded_by_id"] == manual["id"]


def test_labels_resolve_through_the_taxonomy(client: TestClient) -> None:
    target = spontaneous_target(client)

    item = client.post(
        f"/api/targets/{target['id']}/requirements",
        json=manual_body(label="postgres", excerpt="uses postgres"),
    ).json()["requirement"]

    assert (item["key"], item["label"]) == ("postgresql", "PostgreSQL")


# --- Immutability -----------------------------------------------------------------------------


def test_requirement_content_is_immutable_in_the_orm_and_in_the_database(
    client: TestClient, db_session: Session
) -> None:
    target = offer_target(client)
    extract(client, target["id"])
    requirement = db_session.scalars(select(TargetRequirement)).first()
    assert requirement is not None

    requirement.excerpt = "rewritten"
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()
    db_session.rollback()

    for column in ("excerpt", "importance", "key", "source_hash", "origin"):
        with pytest.raises(DatabaseError, match="immutable"):
            db_session.execute(text(f"UPDATE target_requirements SET {column} = {column}"))
        db_session.rollback()


def test_a_requirement_can_only_be_deactivated_not_edited(
    client: TestClient, db_session: Session
) -> None:
    target = offer_target(client)
    extract(client, target["id"])

    db_session.execute(text("UPDATE target_requirements SET active = 0"))  # allowed
    db_session.commit()

    assert requirements(client, target["id"]) == []
    assert len(requirements(client, target["id"], include_inactive=True)) == 4


def test_only_one_active_requirement_per_target_and_key(
    client: TestClient, db_session: Session
) -> None:
    target = offer_target(client)
    extract(client, target["id"])
    row = db_session.scalars(select(TargetRequirement)).first()
    assert row is not None

    clone = TargetRequirement(
        candidate_id=row.candidate_id,
        target_id=row.target_id,
        kind=row.kind,
        key=row.key,
        label=row.label,
        importance=row.importance,
        origin=row.origin,
        source_field=row.source_field,
        source_id=row.source_id,
        source_hash=row.source_hash,
        excerpt=row.excerpt,
    )
    db_session.add(clone)
    with pytest.raises(DatabaseError):
        db_session.flush()
    db_session.rollback()


def test_two_targets_keep_their_own_requirements(client: TestClient) -> None:
    first = offer_target(client)
    second = other_target(client, opportunity={"description_text": "Docker is required."})

    extract(client, first["id"])
    extract(client, second["id"])

    assert set(by_key(requirements(client, first["id"]))) >= {"python"}
    assert set(by_key(requirements(client, second["id"]))) == {"docker"}


# --- Security ---------------------------------------------------------------------------------


def test_the_requirement_and_brief_routes_require_the_api_token(client: TestClient) -> None:
    anonymous = TestClient(client.app)

    for method, path in [
        ("get", "/api/targets/1/requirements"),
        ("post", "/api/targets/1/requirements"),
        ("post", "/api/targets/1/requirements/extract"),
        ("get", "/api/targets/1/personalization-brief"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_nothing_touches_the_network(client: TestClient, no_network: None) -> None:
    target = offer_target(client)

    extract(client, target["id"])
    client.post(f"/api/targets/{target['id']}/requirements", json=manual_body())

    assert len(requirements(client, target["id"])) == 5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Python is required. SQL is a plus.", {"python": "required", "sql": "nice_to_have"}),
        (
            "We are looking for someone who knows Python. SQL is a plus.",
            {"python": "unspecified", "sql": "nice_to_have"},
        ),
        ("Python required, SQL a plus", {"python": "required", "sql": "nice_to_have"}),
        ("Python is required and SQL is a plus.", {"python": "unspecified", "sql": "unspecified"}),
        (
            "Required:\n- Python\n\nNice to have:\n- SQL",
            {"python": "required", "sql": "nice_to_have"},
        ),
    ],
)
def test_importance_end_to_end_only_states_what_the_offer_says(
    client: TestClient, text: str, expected: dict[str, str]
) -> None:
    target = offer_target(client, text)

    items = extract(client, target["id"])["requirements"]

    assert {item["key"]: item["importance"] for item in items} == expected
    assert all(item["excerpt"] in text for item in items)  # exact, heading included when used
