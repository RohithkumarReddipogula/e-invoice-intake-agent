"""Call the LLM with the extraction schema, through the disk cache.

Two providers (INTAKE_LLM_PROVIDER):
- "anthropic" (default): the Claude API via the Anthropic SDK, `messages.parse`.
- "openrouter": OpenRouter's OpenAI-compatible chat completions API with structured outputs
  (`response_format: json_schema, strict`). It reports the cost of every call
  (`usage.cost`), which is stored in the cache entry and used by the eval budget guard.

In offline mode (INTAKE_LLM_OFFLINE=1, set in CI) a cache miss raises OfflineCacheMiss and
no API client is created, so no network call can happen.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
from pydantic import ValidationError

from ..config import Settings
from .cache import DiskCache, cache_key, schema_hash
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from .schema import LlmInvoiceExtraction

MAX_TOKENS = 16000
SCHEMA_SHA = schema_hash(LlmInvoiceExtraction.model_json_schema())

OPENROUTER_TIMEOUT_SECONDS = 600.0  # same as the Anthropic SDK default
OPENROUTER_RETRIES = 2  # same as the Anthropic SDK default: 429, 5xx, network errors
OPENROUTER_BACKOFF_SECONDS = 1.0
# Not supported by strict structured outputs; LlmInvoiceExtraction checks them on the answer.
UNSUPPORTED_SCHEMA_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength",
}  # fmt: skip


class LlmError(Exception):
    """The LLM gave no usable answer. The invoice goes to review with this message."""


class OfflineCacheMiss(LlmError):
    pass


def default_client_factory(settings: Settings) -> Any:
    if settings.llm_provider == "openrouter":
        if settings.openrouter_api_key is None:
            raise LlmError("INTAKE_LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY.")
        return httpx.Client(
            base_url=settings.openrouter_base_url,
            timeout=OPENROUTER_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                # Optional attribution headers (OpenRouter docs).
                "HTTP-Referer": "https://github.com/RohithkumarReddipogula/e-invoice-intake-agent",
                "X-Title": "e-invoice-intake-agent",
            },
        )
    key = settings.anthropic_api_key
    # Without a key in settings the SDK uses its own credential lookup (env, `ant auth`).
    return anthropic.Anthropic(api_key=key.get_secret_value() if key else None)


def strict_schema(schema: Any) -> Any:
    """The Pydantic JSON schema without keywords strict structured outputs reject."""
    if isinstance(schema, dict):
        return {k: strict_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


class LlmExtractor:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] = default_client_factory,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.cache = DiskCache(settings.llm_cache_dir)
        self._client_factory = client_factory
        self._client: Any = None
        self._sleep = sleep

    def key_for(self, text: str) -> str:
        return cache_key(
            provider=self.settings.llm_provider,
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
        if self.settings.llm_provider == "openrouter":
            return self._call_openrouter(text)
        return self._call_anthropic(text)

    # --- Anthropic ---------------------------------------------------------------------

    def _call_anthropic(self, text: str) -> dict[str, Any]:
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
            "provider": "anthropic",
            "model": self.settings.llm_model,
            "prompt_version": PROMPT_VERSION,
            "request_id": getattr(response, "_request_id", None),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "output": response.parsed_output.model_dump(mode="json"),
        }

    # --- OpenRouter --------------------------------------------------------------------

    def openrouter_request(self, text: str) -> dict[str, Any]:
        return {
            "model": self.settings.llm_model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(text)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "invoice_extraction",
                    "strict": True,
                    "schema": strict_schema(LlmInvoiceExtraction.model_json_schema()),
                },
            },
            # Only route to providers that honour response_format (OpenRouter docs).
            "provider": {"require_parameters": True},
        }

    def _call_openrouter(self, text: str) -> dict[str, Any]:
        body = self.openrouter_request(text)
        response = None
        problem = ""
        for attempt in range(OPENROUTER_RETRIES + 1):
            try:
                response = self._client.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                problem = f"LLM API not reachable: {exc}"
            else:
                if response.status_code == 200:
                    break
                problem = f"LLM API error {response.status_code}: {_error_message(response)}"
                if response.status_code != 429 and response.status_code < 500:
                    raise LlmError(problem)  # e.g. 400, 401, 402 (no credits): no retry
            if attempt < OPENROUTER_RETRIES:
                self._sleep(OPENROUTER_BACKOFF_SECONDS * 2**attempt)
        else:
            raise LlmError(problem)

        data = response.json()
        if "error" in data:  # OpenRouter can return 200 with an error object
            raise LlmError(f"LLM API error: {data['error'].get('message', data['error'])}")
        choice = data["choices"][0]
        finish = choice.get("finish_reason")
        content = choice["message"].get("content")
        if finish != "stop" or not content:
            raise LlmError(f"LLM stopped without a usable answer (finish_reason={finish}).")
        try:
            parsed = LlmInvoiceExtraction.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LlmError(f"LLM answer does not match the schema: {exc}") from exc

        usage = data.get("usage") or {}
        return {
            "provider": "openrouter",
            "model": self.settings.llm_model,
            "served_by": data.get("provider"),  # upstream provider OpenRouter routed to
            "prompt_version": PROMPT_VERSION,
            "request_id": data.get("id"),
            "usage": {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                ),
                # What OpenRouter charged for this call, in credits (1 credit = 1 USD).
                "cost_usd": usage.get("cost"),
            },
            "output": parsed.model_dump(mode="json"),
        }


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        return str(error.get("message") or error)[:300]
    except ValueError:
        return response.text[:300]
