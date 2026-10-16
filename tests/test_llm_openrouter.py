"""OpenRouter provider: request shape, answer handling, reported cost. No network, no key."""

import json

import httpx
import pytest

from eval.cost import entry_cost, spent
from intake.config import Settings
from intake.llm import LlmError, LlmExtractor, OfflineCacheMiss
from intake.llm import client as client_module
from intake.llm.client import default_client_factory, strict_schema
from intake.llm.prompt import SYSTEM_PROMPT
from intake.llm.schema import LlmInvoiceExtraction

from .llm_fakes import answer

TEXT = "Rechnung RE-1 ..."
MODEL = "anthropic/claude-haiku-5.5"


def settings(tmp_path, **kwargs) -> Settings:
    defaults = {
        "llm_provider": "openrouter",
        "llm_model": MODEL,
        "llm_offline": False,
        "openrouter_api_key": "sk-or-test-not-real",
        "llm_cache_dir": tmp_path / "cache",
    }
    return Settings(_env_file=None, **(defaults | kwargs))


def completion(content, finish="stop", cost=0.000412):
    return httpx.Response(
        200,
        json={
            "id": "gen-123",
            "provider": "Anthropic",
            "choices": [
                {"finish_reason": finish, "message": {"role": "assistant", "content": content}}
            ],
            "usage": {
                "prompt_tokens": 2400,
                "completion_tokens": 700,
                "completion_tokens_details": {"reasoning_tokens": 120},
                "cost": cost,
            },
        },
    )


GOOD = json.dumps(answer().model_dump(mode="json"))


def extractor(tmp_path, responses, **kwargs):
    """An extractor whose HTTP calls are answered from `responses` (exceptions are raised)."""
    seen, sleeps = [], []

    def handler(request):
        seen.append(request)
        item = responses[len(seen) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    s = settings(tmp_path, **kwargs)

    def factory(s):
        real = default_client_factory(s)  # real headers and base URL, mock transport
        return httpx.Client(
            base_url=real.base_url, headers=real.headers, transport=httpx.MockTransport(handler)
        )

    return LlmExtractor(s, client_factory=factory, sleep=sleeps.append), seen, sleeps


def test_request_shape(tmp_path):
    ex, seen, _ = extractor(tmp_path, [completion(GOOD)])
    ex.extract(TEXT)
    (request,) = seen
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-or-test-not-real"
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert body["messages"][1]["content"] == f"<invoice_text>\n{TEXT}\n</invoice_text>"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    schema_text = json.dumps(fmt["json_schema"]["schema"])
    assert "minimum" not in schema_text and "maximum" not in schema_text
    assert "$defs" in fmt["json_schema"]["schema"]


def test_answer_and_reported_cost_are_cached(tmp_path):
    ex, seen, _ = extractor(tmp_path, [completion(GOOD)])
    assert ex.extract(TEXT) == answer()
    assert ex.extract(TEXT) == answer()  # second time from the cache
    assert len(seen) == 1
    (path,) = (tmp_path / "cache").glob("*.json")
    entry = json.loads(path.read_text())
    assert entry["provider"] == "openrouter"
    assert entry["served_by"] == "Anthropic"
    assert entry["request_id"] == "gen-123"
    assert entry["usage"] == {
        "input_tokens": 2400,
        "output_tokens": 700,
        "reasoning_tokens": 120,
        "cost_usd": 0.000412,
    }
    assert "sk-or-test" not in path.read_text()


def test_budget_uses_the_reported_cost(tmp_path):
    ex, _, _ = extractor(tmp_path, [completion(GOOD, cost=0.0123)])
    ex.extract(TEXT)
    total, calls = spent(tmp_path / "cache")
    assert (round(total, 6), calls) == (0.0123, 1)


def test_entry_cost_fallbacks():
    assert entry_cost({"model": MODEL, "usage": {"cost_usd": 0.5}}) == 0.5
    priced = {"model": MODEL, "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
    assert entry_cost(priced) == pytest.approx(0.10)
    assert entry_cost({"model": "unknown/model", "usage": {"input_tokens": 1}}) is None


def test_unknown_cost_blocks_the_budget(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "x.json").write_text(json.dumps({"model": "unknown/m", "usage": {}}))
    total, _ = spent(tmp_path / "cache")
    assert total >= 10.0


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (completion(GOOD, finish="length"), "finish_reason=length"),
        (completion(None), "finish_reason=stop"),
        (completion("not json"), "does not match the schema"),
        (completion(json.dumps({"invoice_number": {"value": "x", "confidence": 2}})),
         "does not match the schema"),
        (httpx.Response(402, json={"error": {"message": "Insufficient credits"}}),
         "LLM API error 402: Insufficient credits"),
        (httpx.Response(400, json={"error": {"message": "response_format not supported"}}),
         "LLM API error 400"),
        (httpx.Response(200, json={"error": {"message": "upstream failed"}}), "upstream failed"),
    ],
)  # fmt: skip
def test_unusable_answers_raise_and_are_not_cached(tmp_path, response, message):
    ex, seen, _ = extractor(tmp_path, [response])
    with pytest.raises(LlmError, match=message):
        ex.extract(TEXT)
    assert len(seen) == 1  # no retry for these
    assert not list((tmp_path / "cache").glob("*.json"))


@pytest.mark.parametrize(
    "problem",
    [httpx.Response(429, json={}), httpx.Response(503, text="busy"), httpx.ConnectError("x")],
)
def test_transient_problems_are_retried(tmp_path, problem):
    ex, seen, sleeps = extractor(tmp_path, [problem, problem, completion(GOOD)])
    assert ex.extract(TEXT) == answer()
    assert len(seen) == 3
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_two_retries(tmp_path):
    ex, seen, _ = extractor(tmp_path, [httpx.Response(503, text="down")] * 3)
    with pytest.raises(LlmError, match="503"):
        ex.extract(TEXT)
    assert len(seen) == 3


def test_offline_creates_no_http_client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("httpx.Client() was created in offline mode")

    monkeypatch.setattr(client_module.httpx, "Client", forbidden)
    ex = LlmExtractor(settings(tmp_path, llm_offline=True))  # default factory
    with pytest.raises(OfflineCacheMiss):
        ex.extract(TEXT)


def test_missing_key(tmp_path):
    ex = LlmExtractor(settings(tmp_path, openrouter_api_key=None))
    with pytest.raises(LlmError, match="needs OPENROUTER_API_KEY"):
        ex.extract(TEXT)


def test_providers_do_not_share_cache_entries(tmp_path):
    a = LlmExtractor(settings(tmp_path))
    b = LlmExtractor(settings(tmp_path, llm_provider="anthropic"))
    assert a.key_for(TEXT) != b.key_for(TEXT)


def test_key_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env")
    monkeypatch.setenv("INTAKE_LLM_PROVIDER", "openrouter")
    s = Settings(_env_file=None)
    assert s.llm_provider == "openrouter"
    assert s.openrouter_api_key.get_secret_value() == "sk-or-from-env"
    assert "sk-or-from-env" not in repr(s)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert Settings(_env_file=None).openrouter_api_key is None


def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("INTAKE_LLM_PROVIDER", raising=False)
    assert Settings(_env_file=None).llm_provider == "anthropic"


def test_strict_schema_keeps_structure():
    schema = strict_schema(LlmInvoiceExtraction.model_json_schema())
    assert set(schema["required"]) == set(LlmInvoiceExtraction.model_fields)
    assert schema["additionalProperties"] is False
    confidence = schema["$defs"]["TextField"]["properties"]["confidence"]
    assert confidence["type"] == "number" and "minimum" not in confidence
