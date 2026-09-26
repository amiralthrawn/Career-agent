"""Local API protection: Bearer token on /api/*, public /health, Host and CORS restrictions."""

import logging
import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import MIN_API_TOKEN_CHARS, Settings, get_settings
from app.core.database import get_db
from app.main import create_app
from tests.conftest import TEST_API_TOKEN

PROTECTED_PATHS = ["/api/candidate", "/api/candidate/skills", "/api/candidate/proposals"]


def make_client(engine: Engine, headers: dict[str, str] | None = None, **kwargs: str) -> TestClient:
    app = create_app()

    def override_get_db() -> object:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, headers=headers, **kwargs)  # type: ignore[arg-type]


# --- /health public, /api/* protected --------------------------------------------------


def test_health_is_public(engine: Engine) -> None:
    assert make_client(engine).get("/health").status_code == 200


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_api_without_token_is_401(engine: Engine, path: str) -> None:
    response = make_client(engine).get(path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_every_api_route_requires_the_token(engine: Engine) -> None:
    client = make_client(engine)
    paths = [p for p in client.app.openapi()["paths"] if p.startswith("/api/")]  # type: ignore[attr-defined]

    assert len(paths) >= 15
    for path in paths:
        concrete = path.replace("{ingestion_id}", "1").replace("{proposal_id}", "1")
        for method in ("get", "post"):
            response = getattr(client, method)(concrete)
            assert response.status_code in (401, 405), (method, path)


def test_api_with_valid_token_is_accessible(
    engine: Engine, auth_headers: dict[str, str], candidate_payload: dict[str, str]
) -> None:
    client = make_client(engine, auth_headers)

    assert client.post("/api/candidate", json=candidate_payload).status_code == 201
    assert client.get("/api/candidate").status_code == 200


def test_wrong_token_of_the_same_length_is_refused(engine: Engine) -> None:
    other = secrets.token_urlsafe(len(TEST_API_TOKEN))[: len(TEST_API_TOKEN)]
    assert len(other) == len(TEST_API_TOKEN) and other != TEST_API_TOKEN

    response = make_client(engine, {"Authorization": f"Bearer {other}"}).get("/api/candidate")

    assert response.status_code == 401


@pytest.mark.parametrize("scheme", ["Basic", "Token", "bearer-ish"])
def test_other_authorization_schemes_are_refused(engine: Engine, scheme: str) -> None:
    headers = {"Authorization": f"{scheme} {TEST_API_TOKEN}"}

    assert make_client(engine, headers).get("/api/candidate").status_code == 401


def test_token_in_the_query_string_is_not_accepted(engine: Engine) -> None:
    client = make_client(engine)

    assert client.get("/api/candidate", params={"token": TEST_API_TOKEN}).status_code == 401


# --- Fail closed -----------------------------------------------------------------------


def test_missing_api_token_fails_closed(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, auth_headers: dict[str, str]
) -> None:
    monkeypatch.delenv("API_TOKEN")
    get_settings.cache_clear()
    client = make_client(engine, auth_headers)

    assert client.get("/api/candidate").status_code == 503
    assert make_client(engine).get("/api/candidate").status_code == 503  # even with no header
    assert client.get("/health").status_code == 200  # only /health stays available


def test_empty_api_token_is_treated_as_missing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, auth_headers: dict[str, str]
) -> None:
    monkeypatch.setenv("API_TOKEN", "   ")
    get_settings.cache_clear()

    assert make_client(engine, auth_headers).get("/api/candidate").status_code == 503


def test_short_api_token_is_refused_by_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "x" * (MIN_API_TOKEN_CHARS - 1))

    with pytest.raises(ValidationError):
        Settings()


# --- Host header and CORS --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/api/candidate", "/docs"])
def test_unexpected_host_is_refused(
    engine: Engine, auth_headers: dict[str, str], path: str
) -> None:
    client = make_client(engine, auth_headers, base_url="http://evil.example")

    assert client.get(path).status_code == 400


def test_allowed_hosts_are_accepted(engine: Engine, auth_headers: dict[str, str]) -> None:
    for host in ("127.0.0.1", "localhost", "localhost:8000"):
        client = make_client(engine, auth_headers, base_url=f"http://{host}")
        assert client.get("/health").status_code == 200, host


def test_default_allowed_hosts_are_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_HOSTS")

    assert Settings().allowed_host_list == ["127.0.0.1", "localhost"]


def test_cors_is_closed_unless_origins_are_configured(engine: Engine) -> None:
    headers = {"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}

    response = make_client(engine).options("/api/candidate", headers=headers)

    assert "access-control-allow-origin" not in response.headers


def test_cors_only_allows_the_configured_origin(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    client = make_client(engine)

    good = client.get("/health", headers={"Origin": "http://localhost:3000"})
    bad = client.get("/health", headers={"Origin": "https://evil.example"})

    assert good.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in bad.headers


# --- The token never leaks -------------------------------------------------------------


def test_token_never_appears_in_responses_or_logs(
    engine: Engine, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    good = make_client(engine, auth_headers)
    bad = make_client(engine, {"Authorization": "Bearer " + "z" * 40})
    anonymous = make_client(engine)

    with caplog.at_level(logging.DEBUG):
        responses = [
            good.get("/api/candidate"),  # 404: no candidate yet
            good.post("/api/candidate", json={"first_name": "T"}),  # 422
            good.get("/api/candidate/proposals/999"),  # 404
            bad.get("/api/candidate"),  # 401
            anonymous.get("/api/candidate"),  # 401
            good.get("/openapi.json"),
        ]

    for response in responses:
        assert TEST_API_TOKEN not in response.text
        assert TEST_API_TOKEN not in str(response.headers)
    assert TEST_API_TOKEN not in caplog.text


def test_token_is_hidden_in_settings_representations() -> None:
    settings = Settings()

    assert TEST_API_TOKEN not in repr(settings)
    assert TEST_API_TOKEN not in str(settings)
    assert TEST_API_TOKEN not in settings.model_dump_json()
    assert (
        settings.api_token is not None and settings.api_token.get_secret_value() == TEST_API_TOKEN
    )


def test_openapi_declares_bearer_security_only_on_api_routes(engine: Engine) -> None:
    document = make_client(engine).get("/openapi.json").json()

    assert "security" not in document["paths"]["/health"]["get"]
    assert document["paths"]["/api/candidate"]["get"]["security"]
    assert "HTTPBearer" in document["components"]["securitySchemes"]
