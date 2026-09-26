"""Target pipeline through the API: offer flow, spontaneous flow, provenance, absence."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, Contact, ContactChannel, Opportunity, Source, Target
from tests.targets_factory import (
    DOMAIN,
    HR_EMAIL,
    OTHER_DOMAIN,
    PAGE,
    PERSON_EMAIL,
    company_payload,
    email_channel,
    offer_payload,
)

VALID_SIREN = "123456782"  # synthetic number with a valid checksum


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def post_target(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/targets", json=body)
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


def spontaneous(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return post_target(client, company=company_payload(), **overrides)


def with_offer(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return post_target(client, company=company_payload(), opportunity=offer_payload(), **overrides)


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- The two flows, one pipeline -------------------------------------------------------


def test_spontaneous_target_has_no_offer(client: TestClient, with_candidate: None) -> None:
    result = spontaneous(client, contract_type="apprenticeship", relevance_note="Fits my data aims")

    target = result["target"]
    assert result["created"] and result["company_created"] and not result["opportunity_created"]
    assert target["mode"] == "spontaneous" and target["opportunity"] is None
    assert target["contract_type"] == "apprenticeship"
    assert target["relevance_note"] == "Fits my data aims" and target["status"] == "new"
    assert target["company"]["domain"] == DOMAIN


def test_target_with_an_offer(client: TestClient, with_candidate: None) -> None:
    result = with_offer(client)

    target = result["target"]
    assert result["opportunity_created"] and target["mode"] == "offer"
    assert target["opportunity"]["title"] == "Data Intern"
    assert target["opportunity"]["posted_on"] == "2026-05"
    assert target["opportunity"]["posted_on_precision"] == "month"
    assert target["opportunity"]["status"] == "unknown"  # not knowing is not "closed"
    assert target["contract_type"] == "internship"  # taken from the offer when not given


def test_both_flows_expose_exactly_the_same_shape(client: TestClient, with_candidate: None) -> None:
    """The downstream pipeline (analysis, drafting, sending...) depends only on this contract."""
    offer_target = with_offer(client)["target"]
    other = post_target(
        client, company=company_payload(name="Other Corp", website_url=f"https://{OTHER_DOMAIN}")
    )
    spontaneous_target = other["target"]

    assert set(offer_target) == set(spontaneous_target)
    assert set(offer_target["company"]) == set(spontaneous_target["company"])
    assert offer_target["mode"] != spontaneous_target["mode"]
    assert client.get(f"/api/targets/{offer_target['id']}").json()["mode"] == "offer"


def test_a_company_can_have_a_spontaneous_target_and_an_offer_target(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    first = spontaneous(client)
    second = with_offer(client)

    assert first["target"]["id"] != second["target"]["id"]
    assert first["target"]["company"]["id"] == second["target"]["company"]["id"]
    assert not second["company_created"]
    assert count(db_session, Company) == 1 and count(db_session, Target) == 2


def test_creating_the_same_target_twice_is_idempotent(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    first = client.post("/api/targets", json={"company": company_payload()})
    again = client.post("/api/targets", json={"company": company_payload()})

    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["target"]["id"] == first.json()["target"]["id"]
    assert count(db_session, Target) == 1 and count(db_session, Company) == 1


def test_an_existing_company_can_be_targeted_by_id(
    client: TestClient, with_candidate: None
) -> None:
    company_id = spontaneous(client)["target"]["company"]["id"]

    result = post_target(client, company_id=company_id, opportunity=offer_payload())

    assert result["target"]["mode"] == "offer" and not result["company_created"]


def test_no_target_without_a_candidate(client: TestClient) -> None:
    assert client.post("/api/targets", json={"company": company_payload()}).status_code == 404


# --- De-duplication --------------------------------------------------------------------


def test_company_is_matched_by_domain_even_if_the_name_differs(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    spontaneous(client)

    other = post_target(client, company=company_payload(name="Fixture Corporation Group"))

    assert not other["company_created"] and count(db_session, Company) == 1


def test_company_is_matched_by_siren(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    post_target(client, company={"name": "Fixture Corp", "siren": VALID_SIREN})

    again = post_target(client, company={"name": "Renamed Fixture", "siren": VALID_SIREN})

    assert not again["company_created"] and count(db_session, Company) == 1


def test_company_is_matched_by_name_and_city_but_not_across_cities(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    post_target(client, company={"name": "Fixture Corp SAS", "location": "Faketown"})

    same = post_target(client, company={"name": "FIXTURE CORP", "location": "faketown"})
    elsewhere = post_target(client, company={"name": "Fixture Corp", "location": "Otherville"})
    no_city = post_target(client, company={"name": "Fixture Corp"})

    assert not same["company_created"]
    # A wrong merge is worse than a visible duplicate: different or missing city = new company.
    assert elsewhere["company_created"] and no_city["company_created"]
    assert count(db_session, Company) == 3


def test_two_companies_with_the_same_name_but_different_domains_stay_apart(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    post_target(client, company=company_payload())

    other = post_target(client, company=company_payload(website_url=f"https://{OTHER_DOMAIN}"))

    assert other["company_created"] and count(db_session, Company) == 2


def test_an_existing_company_is_never_overwritten(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    spontaneous(client)

    post_target(client, company=company_payload(sector="Something else", name="Fixture Corp"))

    company = db_session.scalars(select(Company)).one()
    assert company.sector == "Synthetic software" and company.name == "Fixture Corp"


def test_offers_are_matched_by_url_then_external_id(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    first = post_target(
        client, company=company_payload(), opportunity=offer_payload(external_id="EXT-1")
    )
    by_url = post_target(
        client, company=company_payload(), opportunity=offer_payload(title="Renamed")
    )
    by_external = post_target(
        client,
        company=company_payload(),
        opportunity=offer_payload(url=f"https://{DOMAIN}/jobs/moved", external_id="EXT-1"),
    )

    assert first["opportunity_created"]
    assert not by_url["opportunity_created"] and not by_external["opportunity_created"]
    assert count(db_session, Opportunity) == 1 and count(db_session, Target) == 1
    # The stored offer keeps its first values: a later source never overwrites them.
    assert db_session.scalars(select(Opportunity)).one().title == "Data Intern"


def test_offers_without_url_or_id_are_matched_on_title_and_location_only(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    bare = {"title": "Data Intern", "location": "Faketown"}
    post_target(client, company=company_payload(), opportunity=bare)

    same = post_target(client, company=company_payload(), opportunity=bare)
    other_city = post_target(
        client, company=company_payload(), opportunity={**bare, "location": "Otherville"}
    )

    assert not same["opportunity_created"] and other_city["opportunity_created"]
    assert count(db_session, Opportunity) == 2


# --- Validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"company_id": 1, "company": company_payload()},
        {"company": {"name": ""}},
        {"company": {"name": "X", "siren": "123456789"}},
        {"company": {"name": "X", "website_url": "javascript:alert(1)"}},
        {"company": {"name": "X", "country_code": "FRA"}},
        {"company": company_payload(), "opportunity": {"title": ""}},
        {"company": company_payload(), "opportunity": {"title": "T", "posted_on": "2026-13"}},
        {"company": company_payload(), "contract_type": "not-a-contract"},
        {
            "company": company_payload(),
            "source": {"kind": "import_file", "label": "x", "reference": "r"},
        },
        {"company": company_payload(), "source": {"kind": "public_page", "label": "x"}},
    ],
)
def test_invalid_target_requests_are_rejected(
    client: TestClient, with_candidate: None, body: dict[str, Any]
) -> None:
    assert client.post("/api/targets", json=body).status_code == 422


def test_unknown_company_id_is_404(client: TestClient, with_candidate: None) -> None:
    assert client.post("/api/targets", json={"company_id": 999}).status_code == 404
    assert client.get("/api/targets/999").status_code == 404


# --- Provenance and absence ------------------------------------------------------------


def test_every_piece_of_information_carries_its_source(
    client: TestClient, with_candidate: None
) -> None:
    result = with_offer(
        client,
        source={"kind": "public_page", "label": "Careers page", "url": f"https://{DOMAIN}/careers"},
        contacts=[
            {"full_name": "Pat Fixture", "role_title": "HR lead", "channels": [email_channel()]}
        ],
    )

    target = result["target"]
    for holder in (target, target["company"], target["opportunity"]):
        assert holder["source"]["kind"] == "public_page" and holder["source"]["retrieved_at"]
        assert holder["source"]["url"] == f"https://{DOMAIN}/careers"
    (link,) = target["contacts"]
    contact = link["contact"]
    assert contact["source"]["label"] == "Careers page"  # the person: the target's source
    assert contact["email"]["source"]["url"] == PAGE  # the address: its own source
    assert contact["email"]["status"] == "found" and contact["email"]["verified"] is False


def test_default_source_is_a_manual_entry(client: TestClient, with_candidate: None) -> None:
    target = spontaneous(client)["target"]

    assert target["source"]["kind"] == "manual" and target["company"]["source"]["kind"] == "manual"


def test_a_contact_without_an_address_reports_null_not_a_guess(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    result = spontaneous(client, contacts=[{"full_name": "Pat Fixture"}])

    (link,) = result["target"]["contacts"]
    assert link["contact"]["email"] is None and link["contact"]["channels"] == []
    # The company has a domain and the contact a name: nothing is derived from them.
    assert count(db_session, ContactChannel) == 0
    assert result["target"]["company"]["contact_research"] == "not_started"


def test_a_channel_without_a_real_source_is_refused(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    no_source = {"kind": "email", "value": HR_EMAIL, "source": {"kind": "manual", "label": "typed"}}

    response = client.post(
        "/api/targets",
        json={
            "company": company_payload(),
            "contacts": [{"full_name": "Pat", "channels": [no_source]}],
        },
    )

    assert response.status_code == 422 and "needs a source" in response.json()["detail"]
    assert count(db_session, ContactChannel) == 0 and count(db_session, Target) == 0


def test_a_manual_channel_needs_a_reference_saying_where_it_comes_from(
    client: TestClient, with_candidate: None
) -> None:
    channel = email_channel(
        source={"kind": "manual", "label": "Business card", "reference": "met at an event"}
    )

    result = spontaneous(client, contacts=[{"full_name": "Pat Fixture", "channels": [channel]}])

    email = result["target"]["contacts"][0]["contact"]["email"]
    assert email["source"]["kind"] == "manual" and email["source"]["reference"] == "met at an event"


@pytest.mark.parametrize(
    "value", ["not-an-email", "a@b", "Name <a@b.invalid>", f"{HR_EMAIL}, other@{DOMAIN}"]
)
def test_invalid_emails_are_refused(client: TestClient, with_candidate: None, value: str) -> None:
    body = {
        "company": company_payload(),
        "contacts": [{"full_name": "Pat", "channels": [email_channel(value)]}],
    }

    assert client.post("/api/targets", json=body).status_code == 422


def test_an_email_from_another_domain_is_only_uncertain(
    client: TestClient, with_candidate: None
) -> None:
    channels = [email_channel(f"pat@{OTHER_DOMAIN}"), email_channel(f"sub@team.{DOMAIN}")]

    result = spontaneous(client, contacts=[{"full_name": "Pat Fixture", "channels": channels}])

    by_value = {c["value"]: c for c in result["target"]["contacts"][0]["contact"]["channels"]}
    assert by_value[f"pat@{OTHER_DOMAIN}"]["status"] == "uncertain"
    assert by_value[f"sub@team.{DOMAIN}"]["status"] == "found"  # a subdomain of the company


def test_the_best_email_is_the_found_one(client: TestClient, with_candidate: None) -> None:
    channels = [email_channel(f"pat@{OTHER_DOMAIN}"), email_channel(PERSON_EMAIL)]

    result = spontaneous(client, contacts=[{"full_name": "Pat Fixture", "channels": channels}])

    assert result["target"]["contacts"][0]["contact"]["email"]["value"] == PERSON_EMAIL


def test_a_shared_mailbox_needs_an_address_and_no_name(
    client: TestClient, with_candidate: None
) -> None:
    assert (
        client.post(
            "/api/targets", json={"company": company_payload(), "contacts": [{"is_generic": True}]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/targets", json={"company": company_payload(), "contacts": [{"role_title": "HR"}]}
        ).status_code
        == 422
    )

    result = spontaneous(client, contacts=[{"is_generic": True, "channels": [email_channel()]}])

    contact = result["target"]["contacts"][0]["contact"]
    assert contact["is_generic"] and contact["full_name"] is None


# --- Contacts: matching, primary, do-not-contact ---------------------------------------


def test_the_same_person_or_address_is_never_duplicated(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    company_id = spontaneous(client)["target"]["company"]["id"]
    body = {"full_name": "Pat Fixture", "channels": [email_channel()]}

    first = client.post(f"/api/companies/{company_id}/contacts", json=body)
    same_name = client.post(
        f"/api/companies/{company_id}/contacts", json={"full_name": "PAT  fixture"}
    )
    same_email = client.post(
        f"/api/companies/{company_id}/contacts",
        json={"full_name": "Someone Else", "channels": [email_channel()]},
    )

    assert (
        first.status_code == 201 and same_name.status_code == 200 and same_email.status_code == 200
    )
    assert same_name.json()["id"] == same_email.json()["id"] == first.json()["id"]
    assert count(db_session, Contact) == 1 and count(db_session, ContactChannel) == 1


def test_attaching_contacts_and_the_primary_rule(client: TestClient, with_candidate: None) -> None:
    target_id = spontaneous(client)["target"]["id"]
    company_id = client.get(f"/api/targets/{target_id}").json()["company"]["id"]

    first = client.post(
        f"/api/targets/{target_id}/contacts", json={"contact": {"full_name": "A One"}}
    )
    second = client.post(
        f"/api/targets/{target_id}/contacts", json={"contact": {"full_name": "B Two"}}
    )
    assert {c["contact"]["full_name"]: c["is_primary"] for c in first.json()["contacts"]} == {
        "A One": True
    }
    assert {c["contact"]["full_name"]: c["is_primary"] for c in second.json()["contacts"]} == {
        "A One": True,
        "B Two": False,
    }

    second_id = second.json()["contacts"][1]["contact"]["id"]
    switched = client.post(
        f"/api/targets/{target_id}/contacts", json={"contact_id": second_id, "is_primary": True}
    )

    primaries = [c["contact"]["full_name"] for c in switched.json()["contacts"] if c["is_primary"]]
    assert primaries == ["B Two"]
    assert len(client.get(f"/api/companies/{company_id}/contacts").json()) == 2


def test_a_contact_of_another_company_cannot_be_attached(
    client: TestClient, with_candidate: None
) -> None:
    target_id = spontaneous(client)["target"]["id"]
    other = post_target(
        client, company={"name": "Other Corp", "website_url": f"https://{OTHER_DOMAIN}"}
    )
    stranger = client.post(
        f"/api/companies/{other['target']['company']['id']}/contacts",
        json={"full_name": "Sam Stranger"},
    ).json()

    response = client.post(
        f"/api/targets/{target_id}/contacts", json={"contact_id": stranger["id"]}
    )

    assert response.status_code == 422 and "another company" in response.json()["detail"]
    assert client.post(f"/api/targets/{target_id}/contacts", json={}).status_code == 422
    assert (
        client.post(f"/api/targets/{target_id}/contacts", json={"contact_id": 999}).status_code
        == 404
    )


def test_contact_flags_can_be_updated(client: TestClient, with_candidate: None) -> None:
    result = spontaneous(client, contacts=[{"full_name": "Pat Fixture"}])
    contact_id = result["target"]["contacts"][0]["contact"]["id"]

    updated = client.patch(
        f"/api/contacts/{contact_id}",
        json={"verified": True, "do_not_contact": True, "status": "uncertain"},
    )

    assert updated.status_code == 200
    assert (
        updated.json()["verified"],
        updated.json()["do_not_contact"],
        updated.json()["status"],
    ) == (
        True,
        True,
        "uncertain",
    )
    assert client.patch(f"/api/contacts/{contact_id}", json={}).status_code == 422
    assert client.patch("/api/contacts/999", json={"verified": True}).status_code == 404


# --- Reads and updates -----------------------------------------------------------------


def test_target_status_lifecycle(client: TestClient, with_candidate: None) -> None:
    target_id = spontaneous(client)["target"]["id"]
    url = f"/api/targets/{target_id}"

    assert client.patch(url, json={"status": "shortlisted"}).json()["status"] == "shortlisted"
    assert client.patch(url, json={"dismissed_reason": "too far"}).status_code == 422
    dismissed = client.patch(
        url, json={"status": "dismissed", "dismissed_reason": "too far"}
    ).json()
    assert dismissed["status"] == "dismissed" and dismissed["dismissed_reason"] == "too far"
    back = client.patch(url, json={"status": "new"}).json()
    assert back["dismissed_reason"] is None
    assert (
        client.patch(url, json={"relevance_note": "  Updated note "}).json()["relevance_note"]
        == "Updated note"
    )
    assert client.patch(url, json={}).status_code == 422


def test_target_filters(client: TestClient, with_candidate: None) -> None:
    offer = with_offer(client, contacts=[{"full_name": "Pat", "channels": [email_channel()]}])[
        "target"
    ]
    spont = spontaneous(client, contract_type="apprenticeship")["target"]  # same company
    other = post_target(
        client,
        company={"name": "Other Corp", "website_url": f"https://{OTHER_DOMAIN}"},
        contacts=[{"full_name": "Sam"}],
    )["target"]
    client.patch(f"/api/targets/{other['id']}", json={"status": "shortlisted"})

    def ids(**params: Any) -> set[int]:
        return {t["id"] for t in client.get("/api/targets", params=params).json()}

    assert ids() == {offer["id"], spont["id"], other["id"]}
    assert ids(mode="offer") == {offer["id"]}
    assert ids(mode="spontaneous") == {spont["id"], other["id"]}
    assert ids(contract_type="apprenticeship") == {spont["id"]}
    assert ids(status="shortlisted") == {other["id"]}
    assert ids(company_id=offer["company"]["id"]) == {offer["id"], spont["id"]}
    assert ids(has_email="true") == {offer["id"]}  # "no address on file", not "has none"
    assert ids(has_email="false") == {spont["id"], other["id"]}
    assert len(client.get("/api/targets", params={"limit": 1}).json()) == 1
    assert client.get("/api/targets", params={"limit": 0}).status_code == 422
    assert client.get("/api/targets", params={"mode": "nope"}).status_code == 422


def test_company_endpoints(client: TestClient, with_candidate: None) -> None:
    target = with_offer(client, contacts=[{"full_name": "Pat Fixture"}])["target"]
    post_target(client, company={"name": "Other Corp", "website_url": f"https://{OTHER_DOMAIN}"})
    company_id = target["company"]["id"]

    listed = client.get("/api/companies").json()
    detail = client.get(f"/api/companies/{company_id}").json()

    assert len(listed) == 2
    assert [c["id"] for c in client.get("/api/companies", params={"q": "fixture"}).json()] == [
        company_id
    ]
    assert [c["id"] for c in client.get("/api/companies", params={"q": "other-corp"}).json()] != [
        company_id
    ]
    assert [o["title"] for o in detail["opportunities"]] == ["Data Intern"]
    assert [c["full_name"] for c in detail["contacts"]] == ["Pat Fixture"]
    assert detail["contact_research"] == "not_started"
    assert client.get("/api/companies/999").status_code == 404
    assert client.get("/api/companies/999/contacts").status_code == 404


def test_all_new_routes_require_the_api_token(client: TestClient, with_candidate: None) -> None:
    anonymous = TestClient(client.app)  # no Authorization header

    calls = [
        ("get", "/api/targets"),
        ("post", "/api/targets"),
        ("get", "/api/targets/1"),
        ("patch", "/api/targets/1"),
        ("post", "/api/targets/1/contacts"),
        ("get", "/api/companies"),
        ("get", "/api/companies/1"),
        ("get", "/api/companies/1/contacts"),
        ("post", "/api/companies/1/contacts"),
        ("patch", "/api/contacts/1"),
        ("post", "/api/imports/targets"),
        ("post", "/api/imports/targets/preview"),
    ]
    for method, path in calls:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_sources_are_created_only_when_used(
    client: TestClient, with_candidate: None, db_session: Session
) -> None:
    spontaneous(client)
    after_first = count(db_session, Source)

    spontaneous(client)  # everything matches: nothing new to attribute

    assert count(db_session, Source) == after_first == 1  # one manual source shared by the records
