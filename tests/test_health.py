from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "career-agent"
    assert set(body) == {"status", "app", "version", "environment"}


def test_health_does_not_require_database(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_database_routes_report_missing_configuration_as_503() -> None:
    # No dependency override here: DATABASE_URL is unset, so the real get_db is used.
    unconfigured = TestClient(create_app())

    assert unconfigured.get("/health").status_code == 200
    assert unconfigured.get("/api/candidate").status_code == 503
