"""Centralised application configuration, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing for the requested operation."""


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

    @field_validator("database_url", mode="before")
    @classmethod
    def _empty_database_url_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def private_data_path(self) -> Path:
        """Absolute path of the private data directory."""
        path = self.private_data_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigurationError(
                "DATABASE_URL is not set. Define it in your environment or in .env."
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
