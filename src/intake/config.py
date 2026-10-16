"""Settings from environment variables and the git-ignored .env file."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # Guardrails for production use, OFF by default (the eval numbers were measured without
    # them). Turn on in the pilot, see docs/rollout-plan.md and src/intake/guardrails.py.
    max_auto_amount: Decimal | None = Field(default=None, gt=0)
    review_new_suppliers: bool = False

    # Times are stored in UTC and shown in this zone on the review page.
    display_timezone: str = "Europe/Berlin"

    # Public demo (src/intake/demo.py): no API key, LLM and KoSIT results only from the
    # committed caches, sample invoices instead of uploads, hourly reset, binds to 0.0.0.0.
    demo_mode: bool = False
    demo_data_dir: Path = Path("eval/data/test")
    kosit_cache_dir: Path = Path("eval/kosit_cache")

    @model_validator(mode="after")
    def _demo_mode_is_locked_down(self) -> Settings:
        if not self.demo_mode:
            return self
        if self.anthropic_api_key is not None or self.openrouter_api_key is not None:
            raise ValueError(
                "INTAKE_DEMO_MODE=1 runs without an API key: unset ANTHROPIC_API_KEY and "
                "OPENROUTER_API_KEY. The demo only uses the committed caches."
            )
        # The configuration the committed LLM cache was filled with (eval, prompt v2).
        self.llm_provider = "openrouter"
        self.llm_model = "anthropic/claude-haiku-5.5"
        self.llm_offline = True
        self.llm_cache_dir = Path("eval/llm_cache")
        # Guardrails off, as in the published eval.
        self.max_auto_amount = None
        self.review_new_suppliers = False
        return self

    @field_validator("anthropic_api_key", "openrouter_api_key", "max_auto_amount", mode="before")
    @classmethod
    def _empty_key_is_no_key(cls, value: object) -> object:
        # `ANTHROPIC_API_KEY=` copied unchanged from .env.example means "no key".
        return None if isinstance(value, str) and not value.strip() else value
