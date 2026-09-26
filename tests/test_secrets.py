"""SecretStore abstraction, the keyring adapter and the secrets CLI (never prints a value)."""

import os
import secrets as stdlib_secrets
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import KeyringError, PasswordDeleteError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.secrets import (
    GMAIL_REFRESH_TOKEN,
    MAX_SECRET_CHARS,
    InMemorySecretStore,
    KeyringSecretStore,
    SecretStoreError,
)
from app.models import AuditEvent
from app.models.audit import AuditEventType
from scripts import manage_secrets

# Synthetic marker: a fake secret value that must never be echoed anywhere.
SENTINEL = "SYNTHETIC-SECRET-" + stdlib_secrets.token_hex(8)


class FakeKeyring(KeyringBackend):
    """In-memory keyring backend, so tests never touch the real credential vault."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.values: dict[tuple[str, str], str] = {}
        self.fail_with: Exception | None = None

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail_with:
            raise self.fail_with
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail_with:
            raise self.fail_with
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.fail_with:
            raise self.fail_with
        if (service, username) not in self.values:
            raise PasswordDeleteError("not found")
        del self.values[(service, username)]


@pytest.fixture
def fake_keyring() -> Iterator[FakeKeyring]:
    original = keyring.get_keyring()
    backend = FakeKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


# --- In-memory store -------------------------------------------------------------------


def test_in_memory_roundtrip() -> None:
    store = InMemorySecretStore()

    assert store.get(GMAIL_REFRESH_TOKEN) is None and not store.exists(GMAIL_REFRESH_TOKEN)
    store.set(GMAIL_REFRESH_TOKEN, SENTINEL)
    assert store.get(GMAIL_REFRESH_TOKEN) == SENTINEL and store.exists(GMAIL_REFRESH_TOKEN)
    store.delete(GMAIL_REFRESH_TOKEN)
    assert store.get(GMAIL_REFRESH_TOKEN) is None
    store.delete(GMAIL_REFRESH_TOKEN)  # deleting an absent secret is not an error


@pytest.mark.parametrize("name", ["", "Upper", "1starts_with_digit", "has space", "a" * 65, "a/b"])
def test_invalid_secret_names_are_refused(name: str) -> None:
    with pytest.raises(SecretStoreError):
        InMemorySecretStore().set(name, "value")


def test_empty_and_oversized_secrets_are_refused_without_echoing_them() -> None:
    store = InMemorySecretStore()

    with pytest.raises(SecretStoreError):
        store.set("some_secret", "")
    with pytest.raises(SecretStoreError) as error:
        store.set("some_secret", SENTINEL + "x" * MAX_SECRET_CHARS)
    assert SENTINEL not in str(error.value)


# --- Keyring adapter -------------------------------------------------------------------


def test_keyring_store_roundtrip_uses_the_service_name(fake_keyring: FakeKeyring) -> None:
    store = KeyringSecretStore(service="career-agent-test")

    store.set(GMAIL_REFRESH_TOKEN, SENTINEL)

    assert fake_keyring.values == {("career-agent-test", GMAIL_REFRESH_TOKEN): SENTINEL}
    assert store.get(GMAIL_REFRESH_TOKEN) == SENTINEL and store.exists(GMAIL_REFRESH_TOKEN)
    store.delete(GMAIL_REFRESH_TOKEN)
    assert store.get(GMAIL_REFRESH_TOKEN) is None
    store.delete(GMAIL_REFRESH_TOKEN)  # absent: fine


def test_keyring_errors_never_leak_the_secret(fake_keyring: FakeKeyring) -> None:
    store = KeyringSecretStore(service="career-agent-test")
    fake_keyring.fail_with = KeyringError(f"backend exploded with {SENTINEL}")

    actions: list[Callable[[], object]] = [
        lambda: store.set("some_secret", SENTINEL),
        lambda: store.get("some_secret"),
        lambda: store.delete("some_secret"),
    ]
    for action in actions:
        with pytest.raises(SecretStoreError) as error:
            action()
        assert SENTINEL not in str(error.value)
        assert error.value.__suppress_context__


def test_no_secure_backend_means_no_store_and_no_plaintext_fallback() -> None:
    original = keyring.get_keyring()
    try:
        keyring.set_keyring(fail.Keyring())  # type: ignore[no-untyped-call]
        with pytest.raises(SecretStoreError, match="never stored in plain text"):
            KeyringSecretStore()

        plaintext = FakeKeyring()
        type(plaintext).__module__ = "keyrings.alt.file"  # what a plain-text backend looks like
        keyring.set_keyring(plaintext)
        with pytest.raises(SecretStoreError, match="never stored in plain text"):
            KeyringSecretStore()
    finally:
        FakeKeyring.__module__ = __name__
        keyring.set_keyring(original)


@pytest.mark.skipif(
    os.environ.get("CAREER_AGENT_TEST_REAL_KEYRING") != "1",
    reason="opt-in: set CAREER_AGENT_TEST_REAL_KEYRING=1 to test the real OS credential vault",
)
def test_real_credential_vault_roundtrip() -> None:
    service = f"career-agent-test-{uuid.uuid4().hex[:8]}"
    store = KeyringSecretStore(service=service)
    try:
        store.set("roundtrip_probe", SENTINEL)
        assert store.get("roundtrip_probe") == SENTINEL
    finally:
        store.delete("roundtrip_probe")
    assert store.get("roundtrip_probe") is None


# --- CLI: never prints a secret ---------------------------------------------------------


def test_cli_set_and_status_never_print_the_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = InMemorySecretStore()

    assert (
        manage_secrets.main(
            ["set", GMAIL_REFRESH_TOKEN], store=store, read_secret=lambda _: SENTINEL
        )
        == 0
    )
    assert manage_secrets.main(["status"], store=store) == 0

    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert store.get(GMAIL_REFRESH_TOKEN) == SENTINEL
    assert f"{GMAIL_REFRESH_TOKEN}: set" in captured.out
    assert "openrouter_api_key: not set" in captured.out


def test_cli_value_is_never_a_command_line_argument() -> None:
    with pytest.raises(SystemExit):  # `set` takes a name only
        manage_secrets.main(["set", GMAIL_REFRESH_TOKEN, SENTINEL], store=InMemorySecretStore())


def test_cli_delete_and_invalid_names(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemorySecretStore()
    store.set("openrouter_api_key", SENTINEL)

    assert manage_secrets.main(["delete", "openrouter_api_key"], store=store) == 0
    assert store.get("openrouter_api_key") is None
    assert manage_secrets.main(["delete", "Bad Name"], store=store) == 1
    assert SENTINEL not in capsys.readouterr().out


def test_cli_changes_are_audited_by_name_only(
    configured_database: Callable[[], Session], capsys: pytest.CaptureFixture[str]
) -> None:
    store = InMemorySecretStore()

    manage_secrets.main(["set", GMAIL_REFRESH_TOKEN], store=store, read_secret=lambda _: SENTINEL)
    manage_secrets.main(["delete", GMAIL_REFRESH_TOKEN], store=store)

    with configured_database() as session:
        events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    assert [e.event_type for e in events] == [
        AuditEventType.SECRET_SET,
        AuditEventType.SECRET_DELETED,
    ]
    assert all(e.subject == f"secret:{GMAIL_REFRESH_TOKEN}" and e.actor == "cli" for e in events)
    assert SENTINEL not in repr([(e.subject, e.details) for e in events])
    assert SENTINEL not in capsys.readouterr().out


def test_cli_warns_when_the_audit_cannot_be_recorded(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemorySecretStore()  # no database configured in this test

    assert (
        manage_secrets.main(["set", "some_secret"], store=store, read_secret=lambda _: SENTINEL)
        == 0
    )

    assert "could not be recorded in the audit trail" in capsys.readouterr().err


# --- CLI: API token generation ----------------------------------------------------------


def read_token(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_TOKEN="):
            return line.split("=", 1)[1]
    return ""


def test_init_api_token_writes_env_without_printing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=\nAPI_TOKEN=\nSEND_MODE=disabled\n", encoding="utf-8")

    assert manage_secrets.main(["init-api-token"], env_file=env_file) == 0

    token = read_token(env_file)
    assert len(token) >= 32
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert "SEND_MODE=disabled" in env_file.read_text(encoding="utf-8")  # other lines untouched
    assert Settings(_env_file=env_file).api_token is not None


def test_init_api_token_does_not_overwrite_unless_rotating(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    manage_secrets.main(["init-api-token"], env_file=env_file)
    first = read_token(env_file)

    assert manage_secrets.main(["init-api-token"], env_file=env_file) == 1
    assert read_token(env_file) == first
    assert manage_secrets.main(["init-api-token", "--rotate"], env_file=env_file) == 0
    assert read_token(env_file) not in ("", first)


def test_init_api_token_creates_env_from_the_example(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"

    manage_secrets.main(["init-api-token"], env_file=env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "SEND_MODE=disabled" in text and read_token(env_file)


def test_read_secret_helper_signature_is_hidden_input() -> None:
    import getpass

    assert manage_secrets.main.__kwdefaults__ is not None
    assert manage_secrets.main.__kwdefaults__["read_secret"] is getpass.getpass
