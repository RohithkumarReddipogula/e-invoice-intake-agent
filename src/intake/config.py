"""Settings from environment variables and the git-ignored .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INTAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The key keeps its standard name (no INTAKE_ prefix) so the Anthropic tooling finds it too.
    # SecretStr keeps it out of repr(), logs and tracebacks.
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, alias="OPENROUTER_API_KEY")

    # "anthropic": Claude API directly (default). "openrouter": OpenRouter's OpenAI-compatible
    # API, where model IDs look like "anthropic/claude-sonnet-5.5".
    llm_provider: Literal["anthropic", "openrouter"] = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # In offline mode an LLM cache miss is an error and no API client is created. CI sets this.
    llm_offline: bool = False
    llm_cache_dir: Path = Path("llm_cache")

    confidence_threshold: float = Field(default=0.9, ge=0.0, le=1.0)

    database_path: Path = Path("data/intake.db")
    files_dir: Path = Path("data/files")
    max_upload_bytes: int = 20 * 1024 * 1024

    # Mock ERP from `python -m mock_erp`. Empty: no export, approved invoices stay approved.
    erp_base_url: str | None = "http://127.0.0.1:8001"

    # Times are stored in UTC and shown in this zone on the review page.
    display_timezone: str = "Europe/Berlin"

    @field_validator("anthropic_api_key", "openrouter_api_key", mode="before")
    @classmethod
    def _empty_key_is_no_key(cls, value: object) -> object:
        # `ANTHROPIC_API_KEY=` copied unchanged from .env.example means "no key".
        return None if isinstance(value, str) and not value.strip() else value
