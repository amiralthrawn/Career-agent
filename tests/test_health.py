from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "career-agent"
    assert set(body) == {"status", "app", "version", "environment"}


def test_health_does_not_require_database(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
