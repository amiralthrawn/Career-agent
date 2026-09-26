"""Guards making sure no real personal data ends up in the repository."""

import re
import subprocess
from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT
from tests.conftest import CANDIDATE_PAYLOAD

SCANNED_SUFFIXES = {".py", ".md", ".toml", ".ini", ".example"}
SKIPPED_DIRS = {".venv", ".git", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
SKIPPED_DIRS |= {".pytest_cache", "career_agent.egg-info", "private"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ALLOWED_EMAIL_DOMAINS = {"example.invalid", "example.com", "example.org"}
PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?\d(?:[\s.-]?\d){7,}|0[1-9](?:[\s.-]?\d{2}){4})")


def scanned_files() -> list[Path]:
    return [
        path
        for path in PROJECT_ROOT.rglob("*")
        if path.is_file()
        and path.suffix in SCANNED_SUFFIXES
        and not SKIPPED_DIRS & set(path.relative_to(PROJECT_ROOT).parts)
    ]


def test_scan_covers_the_code_base() -> None:
    names = {path.name for path in scanned_files()}

    assert {"candidate.py", "facts.py", "conftest.py", "README.md"} <= names


def test_no_email_address_is_hardcoded() -> None:
    offenders = []
    for path in scanned_files():
        for match in EMAIL.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            domain = match.group().rsplit("@", 1)[1].lower()
            # `.invalid` is a reserved top-level domain (RFC 2606): it can never be a real one.
            if domain not in ALLOWED_EMAIL_DOMAINS and not domain.endswith(".invalid"):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {match.group()}")

    assert offenders == []


def test_no_phone_number_is_hardcoded() -> None:
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in scanned_files()
        if PHONE.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]

    assert offenders == []


def test_fixture_identity_is_obviously_synthetic() -> None:
    assert CANDIDATE_PAYLOAD["email"].endswith("@example.invalid")
    assert CANDIDATE_PAYLOAD["first_name"] == "Test"
    assert "phone" not in CANDIDATE_PAYLOAD


def test_models_define_no_default_identity_values() -> None:
    from app.models import Candidate

    for column in Candidate.__table__.columns:
        assert column.default is None, f"{column.name} must not have a hardcoded default"


@pytest.mark.skipif(not (PROJECT_ROOT / ".git").exists(), reason="not a git checkout")
def test_no_private_file_is_tracked_by_git() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "data/private", ".env"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert tracked == ""
