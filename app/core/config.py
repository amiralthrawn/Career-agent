"""Centralised application configuration, loaded from environment variables / .env."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_API_TOKEN_CHARS = 32


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing for the requested operation."""


class SendMode(StrEnum):
    """What the application is allowed to do with outgoing e-mail.

    - `disabled` (default): nothing is ever sent, not even written to disk.
    - `dry_run`: messages are only written as .eml files under the private outbox.
    - `manual`: a real send needs a human-approved draft and an allowed recipient (a narrow
      allow-list, meant for the first bootstrapping sends to your own address).
    - `auto` (step 10): a real send needs an explicitly approved `ApplicationPackage` AND an
      explicitly approved `SendBatch` (individual or batch of one) - no recipient allow-list,
      since the trust boundary is the explicit two-layer human approval, not a fixed address
      list. Still fails closed: no live provider configured means nothing is ever sent.
    """

    DISABLED = "disabled"
    DRY_RUN = "dry_run"
    MANUAL = "manual"
    AUTO = "auto"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "career-agent"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False

    # Full SQLAlchemy URL, e.g. postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME.
    # Optional so the API and tests can run without a database.
    database_url: str | None = Field(default=None, repr=False)

    private_data_dir: Path = Path("data/private")

    # Comma-separated origins allowed to call the API (future Next.js frontend).
    cors_origins: str = ""

    # --- Local API security -------------------------------------------------------------
    # Bearer token required on every /api/* route. Never logged, never returned.
    # Without it the API answers 503 (fail closed).
    api_token: SecretStr | None = Field(default=None, repr=False)
    # Host headers accepted by the API (protects against DNS rebinding).
    allowed_hosts: str = "127.0.0.1,localhost"

    # --- Outgoing e-mail ----------------------------------------------------------------
    send_mode: SendMode = SendMode.DISABLED
    # Comma-separated exact addresses that a real send may target (e.g. your own address).
    send_allowed_recipients: str = ""
    # Sender address of outgoing messages (kept in the environment, never in code).
    mail_from: str | None = None

    # --- LLM drafts (step 4) -------------------------------------------------------------
    # Master switch: false by default, so nothing depends on OpenRouter in production yet.
    # The API key itself lives in the SecretStore (`openrouter_api_key`), never here.
    llm_enabled: bool = False
    # Only sets the DEFAULT model of a generation request; never a benchmark or routing rule.
    openrouter_model: str | None = None

    # --- External research, Perplexity (step 6) -------------------------------------------
    # Master switch: false by default, so nothing depends on Perplexity in production yet.
    # The API key itself lives in the SecretStore (`perplexity_api_key`), never here.
    research_enabled: bool = False
    # Only sets the DEFAULT preset/model of a research call; never a routing or ranking rule.
    perplexity_preset: str | None = None

    # --- GitHub public research (step 9) --------------------------------------------------
    # Master switch: false by default, so nothing depends on GitHub in production yet.
    # An optional token lives in the SecretStore (`github_token`), never here; it only raises
    # the unauthenticated rate limit and is never required (public data only).
    github_enabled: bool = False
    # The candidate's own public GitHub username. No default: never bake a real identifier in.
    github_username: str | None = None

    @field_validator(
        "database_url",
        "api_token",
        "mail_from",
        "openrouter_model",
        "perplexity_preset",
        "github_username",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("api_token")
    @classmethod
    def _api_token_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_API_TOKEN_CHARS:
            raise ValueError(f"API_TOKEN must have at least {MIN_API_TOKEN_CHARS} characters")
        return value

    @property
    def private_data_path(self) -> Path:
        """Absolute path of the private data directory."""
        path = self.private_data_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def outbox_path(self) -> Path:
        """Where dry-run .eml files are written (always inside the private directory)."""
        return self.private_data_path / "outbox"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def allowed_recipient_list(self) -> list[str]:
        return [
            item.strip().lower() for item in self.send_allowed_recipients.split(",") if item.strip()
        ]

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigurationError(
                "DATABASE_URL is not set. Define it in your environment or in .env."
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
