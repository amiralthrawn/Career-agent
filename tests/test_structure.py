import subprocess
from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT

REQUIRED_PATHS = [
    "app/api",
    "app/core",
    "app/models",
    "app/schemas",
    "app/services",
    "app/agents",
    "app/main.py",
    "data/private/profile",
    "data/private/documents",
    "data/private/portfolio",
    "data/private/applications",
    "tests",
    "docs",
    "scripts",
    ".env.example",
    ".gitignore",
    "README.md",
    "pyproject.toml",
    "alembic.ini",
    "migrations/env.py",
    "docs/security.md",
    "docs/mail-architecture.md",
    "docs/targets.md",
    "docs/qualification.md",
    "docs/requirements.md",
    "app/data/skill_taxonomy.json",
    "scripts/run_api.py",
    "scripts/manage_secrets.py",
    "scripts/dry_run_mail.py",
]


@pytest.mark.parametrize("relative", REQUIRED_PATHS)
def test_required_path_exists(relative: str) -> None:
    assert (PROJECT_ROOT / relative).exists()


def test_env_example_contains_no_values_for_secrets() -> None:
    lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = dict(
        line.split("=", 1) for line in lines if line.strip() and not line.startswith("#")
    )

    assert assignments["DATABASE_URL"] == ""
    # No secret or personal value may ever be in the example file.
    assert assignments["API_TOKEN"] == ""
    assert assignments["MAIL_FROM"] == "" and assignments["SEND_ALLOWED_RECIPIENTS"] == ""
    assert assignments["SEND_MODE"] == "disabled"


def test_alembic_config_does_not_hardcode_database_url() -> None:
    ini = (PROJECT_ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert not any(line.strip().startswith("sqlalchemy.url") for line in ini.splitlines())


def test_alembic_scripts_load() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    assert ScriptDirectory.from_config(config).get_heads() == ["0007"]


@pytest.mark.skipif(not (PROJECT_ROOT / ".git").exists(), reason="not a git checkout")
@pytest.mark.parametrize(
    "relative",
    [
        "data/private/profile/cv.pdf",
        ".env",
        "data/private/x",
        "data/private/documents/cv.docx",
        "data/private/documents/~$cv.docx",
        "data/private/applications/extracted-proposals.json",
        "data/private/reviews/cv-proposals-preview-deadbeefcafe.html",
        "data/private/outbox/20260101T000000-deadbeefca.eml",
        "data/private/outbox/anything.eml.part",
    ],
)
def test_private_files_are_git_ignored(relative: str) -> None:
    result = subprocess.run(["git", "check-ignore", "-q", relative], cwd=PROJECT_ROOT, check=False)

    assert result.returncode == 0, f"{relative} is not ignored by Git"


@pytest.mark.skipif(not (PROJECT_ROOT / ".git").exists(), reason="not a git checkout")
def test_env_example_is_not_git_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=PROJECT_ROOT, check=False
    )

    assert result.returncode == 1


FORBIDDEN_IMPORTS = (
    "langchain",
    "langgraph",
    "playwright",
    # Step 1 makes no network call: no HTTP/mail client, no Google, no OpenRouter/LLM SDK.
    "smtplib",
    "imaplib",
    "poplib",
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
    "googleapiclient",
    "google.oauth2",
    "google_auth_oauthlib",
    "openai",
    "anthropic",
)


def test_business_layers_do_not_import_forbidden_frameworks() -> None:
    for path in Path(PROJECT_ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_IMPORTS:
            assert f"import {name}" not in source and f"from {name}" not in source, (path, name)
