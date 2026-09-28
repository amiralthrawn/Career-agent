"""Synthetic builders for the "Criteria v1" tests (step 3a). Every value is fictional."""

from typing import Any

from fastapi.testclient import TestClient

from tests.targets_factory import DOMAIN


def add_target(
    client: TestClient,
    *,
    contract_type: str | None = "apprenticeship",
    title: str | None = "Data Analyst Alternance",
    description: str | None = "You will work as a data analyst on our BI dashboards.",
    location: str | None = "Paris",
    remote_mode: str | None = None,
    country_code: str | None = "FR",
    company_location: str | None = None,
    domain: str = DOMAIN,
    name: str = "Fixture Corp",
    offer: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "company": {
            "name": name,
            "website_url": f"https://{domain}",
            "location": company_location if company_location is not None else location,
            "country_code": country_code,
            "sector": "Synthetic industry",
        }
    }
    if contract_type is not None:
        body["contract_type"] = contract_type
    if offer:
        opportunity: dict[str, Any] = {"title": title, "url": f"https://{domain}/jobs/1"}
        if description is not None:
            opportunity["description_text"] = description
        if location is not None:
            opportunity["location"] = location
        if remote_mode is not None:
            opportunity["remote_mode"] = remote_mode
        if contract_type is not None:
            opportunity["contract_type"] = contract_type
        body["opportunity"] = opportunity
    response = client.post("/api/targets", json=body)
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()["target"]
    return result


def run(client: TestClient, target_id: int) -> Any:
    return client.post(f"/api/opportunities/{target_id}/qualification")


def show(client: TestClient, target_id: int) -> Any:
    return client.get(f"/api/opportunities/{target_id}/qualification")


def qualified(client: TestClient) -> Any:
    return client.get("/api/opportunities/qualified")


def uncertain(client: TestClient) -> Any:
    return client.get("/api/opportunities/uncertain")
