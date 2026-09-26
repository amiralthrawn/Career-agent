"""Synthetic builders for the qualification tests (API level). Everything is fictional."""

from typing import Any

from fastapi.testclient import TestClient

from tests.targets_factory import DOMAIN, OTHER_DOMAIN


def crit(
    dimension: str,
    values: list[str],
    level: str = "required",
    operator: str = "any_of",
    **extra: Any,
) -> dict[str, Any]:
    return {"dimension": dimension, "values": values, "level": level, "operator": operator, **extra}


def make_profile(
    client: TestClient,
    criteria: list[dict[str, Any]],
    name: str = "Fixture profile",
    **fields: Any,
) -> dict[str, Any]:
    response = client.post(
        "/api/search-profiles", json={"name": name, "criteria": criteria, **fields}
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def add_target(
    client: TestClient,
    *,
    offer: bool = True,
    company: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    contract_type: str | None = "apprenticeship",
    domain: str = DOMAIN,
    name: str = "Fixture Corp",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "company": {
            "name": name,
            "website_url": f"https://{domain}",
            "location": "Faketown",
            "country_code": "FR",
            "sector": "Synthetic software",
            **(company or {}),
        },
    }
    if contract_type:
        body["contract_type"] = contract_type
    if offer:
        body["opportunity"] = {
            "title": "Data Analyst Intern",
            "url": f"https://{domain}/jobs/1",
            "location": "Faketown",
            "description_text": "You will work with Python and SQL on synthetic dashboards.",
            **(opportunity or {}),
        }
    response = client.post("/api/targets", json=body)
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()["target"]
    return result


def other_target(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """A second, different company."""
    return add_target(client, domain=OTHER_DOMAIN, name="Other Corp", **kwargs)


def qualify(client: TestClient, target_id: int, **body: Any) -> Any:
    return client.post(f"/api/targets/{target_id}/qualify", json=body)


def qualification(client: TestClient, target_id: int) -> dict[str, Any]:
    response = client.get(f"/api/targets/{target_id}/qualification")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def outcomes(data: dict[str, Any]) -> dict[str, str]:
    """dimension -> outcome (one criterion per dimension in the tests that use this)."""
    return {r["criterion"]["dimension"]: r["outcome"] for r in data["results"]}


def reason_codes(data: dict[str, Any]) -> list[str]:
    return [reason["code"] for reason in data["reasons"]]
