"""Call Claude with the extraction schema, through the disk cache.

In offline mode (INTAKE_LLM_OFFLINE=1, set in CI) a cache miss raises OfflineCacheMiss and
the Anthropic client is never created, so no network call can happen.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anthropic
from pydantic import ValidationError

from ..config import Settings
from .cache import DiskCache, cache_key, schema_hash
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from .schema import LlmInvoiceExtraction

MAX_TOKENS = 16000
SCHEMA_SHA = schema_hash(LlmInvoiceExtraction.model_json_schema())


class LlmError(Exception):
    """The LLM gave no usable answer. The invoice goes to review with this message."""


class OfflineCacheMiss(LlmError):
    pass


def default_client_factory(settings: Settings) -> Any:
    key = settings.anthropic_api_key
    # Without a key in settings the SDK uses its own credential lookup (env, `ant auth`).
    return anthropic.Anthropic(api_key=key.get_secret_value() if key else None)


class LlmExtractor:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] = default_client_factory,
    ):
        self.settings = settings
        self.cache = DiskCache(settings.llm_cache_dir)
        self._client_factory = client_factory
        self._client: Any = None

    def key_for(self, text: str) -> str:
        return cache_key(
            model=self.settings.llm_model,
            prompt_version=PROMPT_VERSION,
            schema_sha=SCHEMA_SHA,
            text=text,
        )

    def extract(self, text: str) -> LlmInvoiceExtraction:
        key = self.key_for(text)
        entry = self.cache.get(key)
        if entry is None:
            if self.settings.llm_offline:
                raise OfflineCacheMiss(
                    f"No cached LLM answer (key {key[:12]}) and offline mode is on."
                )
            entry = self._call(text)
            self.cache.put(key, entry)
        try:
            return LlmInvoiceExtraction.model_validate(entry["output"])
        except (KeyError, ValidationError) as exc:
            raise LlmError(
                f"Cached LLM answer {key[:12]} does not match the schema: {exc}"
            ) from exc

    def _call(self, text: str) -> dict[str, Any]:
        if self._client is None:
            self._client = self._client_factory(self.settings)
        try:
            response = self._client.messages.parse(
                model=self.settings.llm_model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message(text)}],
                output_format=LlmInvoiceExtraction,
            )
        except ValidationError as exc:
            raise LlmError(f"LLM answer does not match the schema: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(f"LLM API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError(f"LLM API not reachable: {exc}") from exc

        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise LlmError(
                f"LLM stopped without a usable answer (stop_reason={response.stop_reason})."
            )

        return {
            "model": self.settings.llm_model,
            "prompt_version": PROMPT_VERSION,
            "request_id": getattr(response, "_request_id", None),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "output": response.parsed_output.model_dump(mode="json"),
        }
