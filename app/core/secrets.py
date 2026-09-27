"""Secret storage behind a small abstraction.

The rest of the application depends on `SecretStore`, never on `keyring` directly. The real
implementation uses the operating system's credential vault (Windows Credential Manager).
There is deliberately NO fallback to a plain-text file: if no secure backend is available the
store refuses to work.

Secret values are never logged and never included in exception messages.
"""

import re
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE_NAME = "career-agent"

# Names of the secrets the application knows about (values are never stored in code).
GMAIL_REFRESH_TOKEN = "gmail_refresh_token"
GMAIL_CLIENT_SECRET = "gmail_client_secret"
OPENROUTER_API_KEY = "openrouter_api_key"
PERPLEXITY_API_KEY = "perplexity_api_key"
# Optional: raises GitHub's unauthenticated rate limit. Never required (public data only).
GITHUB_TOKEN = "github_token"
KNOWN_SECRETS = (
    GMAIL_REFRESH_TOKEN,
    GMAIL_CLIENT_SECRET,
    OPENROUTER_API_KEY,
    PERPLEXITY_API_KEY,
    GITHUB_TOKEN,
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Windows Credential Manager stores a credential in at most 2560 bytes (UTF-16): stay well below.
MAX_SECRET_CHARS = 1000


class SecretStoreError(RuntimeError):
    """The secret store is unavailable or was misused. Messages never contain secret values."""


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def exists(self, name: str) -> bool: ...


def validate_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise SecretStoreError("Invalid secret name (lowercase letters, digits and underscores)")
    return name


def validate_value(value: str) -> str:
    if not value:
        raise SecretStoreError("A secret cannot be empty")
    if len(value) > MAX_SECRET_CHARS:
        raise SecretStoreError(f"A secret is limited to {MAX_SECRET_CHARS} characters")
    return value


class InMemorySecretStore:
    """Non-persistent store for tests and short-lived tools."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(validate_name(name))

    def set(self, name: str, value: str) -> None:
        self._values[validate_name(name)] = validate_value(value)

    def delete(self, name: str) -> None:
        self._values.pop(validate_name(name), None)

    def exists(self, name: str) -> bool:
        return validate_name(name) in self._values


class KeyringSecretStore:
    """Secrets in the OS credential vault, under the `career-agent` service name."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self._service = service
        self._ensure_secure_backend()

    @staticmethod
    def _ensure_secure_backend() -> None:
        backend = keyring.get_keyring()
        module = type(backend).__module__
        # `fail`/`null` backends store nothing; `keyrings.alt` offers plain-text/file backends.
        if getattr(backend, "priority", 0) <= 0 or module.startswith(
            ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")
        ):
            raise SecretStoreError(
                "No secure keyring backend is available; secrets are never stored in plain text"
            )

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(self._service, validate_name(name))
        except KeyringError:
            raise SecretStoreError("The keyring could not be read") from None

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(self._service, validate_name(name), validate_value(value))
        except KeyringError:
            raise SecretStoreError("The keyring could not be written") from None

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self._service, validate_name(name))
        except PasswordDeleteError:
            return  # already absent
        except KeyringError:
            raise SecretStoreError("The keyring could not be modified") from None

    def exists(self, name: str) -> bool:
        return self.get(name) is not None


def get_secret_store() -> SecretStore:
    return KeyringSecretStore()
