"""Cache and offline behaviour of the LLM client. No test here touches the network."""

import json

import anthropic
import pytest
from pydantic import ValidationError

from intake.config import Settings
from intake.llm import LlmError, LlmExtractor, OfflineCacheMiss
from intake.llm.client import default_client_factory
from intake.llm.schema import LlmInvoiceExtraction

from .llm_fakes import FakeClient, answer

TEXT = "Rechnung RE-1 ..."


def settings(tmp_path, **kwargs) -> Settings:
    # Online by default here (the fake client is the "API"); CI sets INTAKE_LLM_OFFLINE=1
    # for everything else.
    kwargs.setdefault("llm_offline", False)
    return Settings(_env_file=None, llm_cache_dir=tmp_path / "cache", **kwargs)


def no_client(_settings):
    raise AssertionError("an API client was created")


def test_miss_calls_the_api_once_then_uses_the_cache(tmp_path):
    fake = FakeClient()
    extractor = LlmExtractor(settings(tmp_path), client_factory=lambda s: fake)
    first = extractor.extract(TEXT)
    second = extractor.extract(TEXT)
    assert first == second == answer()
    assert len(fake.requests) == 1


def test_cache_hit_creates_no_client(tmp_path):
    LlmExtractor(settings(tmp_path), client_factory=lambda s: FakeClient()).extract(TEXT)
    offline = LlmExtractor(settings(tmp_path, llm_offline=True), client_factory=no_client)
    assert offline.extract(TEXT) == answer()


def test_offline_miss_raises_and_creates_no_client(tmp_path):
    extractor = LlmExtractor(settings(tmp_path, llm_offline=True), client_factory=no_client)
    with pytest.raises(OfflineCacheMiss, match="offline mode"):
        extractor.extract(TEXT)


def test_offline_miss_never_builds_the_real_sdk_client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("anthropic.Anthropic() was called in offline mode")

    monkeypatch.setattr(anthropic, "Anthropic", forbidden)
    extractor = LlmExtractor(settings(tmp_path, llm_offline=True))  # default factory
    with pytest.raises(OfflineCacheMiss):
        extractor.extract(TEXT)


def test_request_shape(tmp_path):
    fake = FakeClient()
    LlmExtractor(settings(tmp_path, llm_model="claude-haiku-5-5"), lambda s: fake).extract(TEXT)
    (request,) = fake.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_format"] is LlmInvoiceExtraction
    assert "untrusted" in request["system"]
    assert request["messages"] == [
        {"role": "user", "content": f"<invoice_text>\n{TEXT}\n</invoice_text>"}
    ]


def test_cache_entry_content(tmp_path):
    s = settings(tmp_path, anthropic_api_key="sk-test-secret")
    extractor = LlmExtractor(s, client_factory=lambda s: FakeClient())
    extractor.extract(TEXT)
    (path,) = (tmp_path / "cache").glob("*.json")
    raw = path.read_text()
    entry = json.loads(raw)
    assert path.stem == extractor.key_for(TEXT)
    assert entry["model"] == "claude-sonnet-5-5"
    assert entry["usage"] == {"input_tokens": 1200, "output_tokens": 450}
    assert entry["output"]["invoice_number"]["value"] == "RE-2026-0042"
    assert "sk-test-secret" not in raw


def test_cache_key_depends_on_model_and_text(tmp_path):
    a = LlmExtractor(settings(tmp_path, llm_model="claude-sonnet-5-5"))
    b = LlmExtractor(settings(tmp_path, llm_model="claude-haiku-5-5"))
    assert a.key_for(TEXT) != b.key_for(TEXT)
    assert a.key_for(TEXT) != a.key_for(TEXT + " ")


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_answer_raises_and_is_not_cached(tmp_path, stop_reason):
    extractor = LlmExtractor(settings(tmp_path), lambda s: FakeClient(stop_reason=stop_reason))
    with pytest.raises(LlmError, match=stop_reason):
        extractor.extract(TEXT)
    assert not list((tmp_path / "cache").glob("*.json"))


def test_schema_mismatch_from_the_api_raises(tmp_path):
    fake = FakeClient()

    def invalid(**kwargs):
        LlmInvoiceExtraction.model_validate({"invoice_number": {"value": "x", "confidence": 2}})

    fake.messages.parse = invalid
    with pytest.raises(LlmError, match="does not match the schema"):
        LlmExtractor(settings(tmp_path), lambda s: fake).extract(TEXT)


def test_corrupt_cache_entry_raises(tmp_path):
    extractor = LlmExtractor(settings(tmp_path, llm_offline=True))
    extractor.cache.put(extractor.key_for(TEXT), {"output": {"invoice_number": "oops"}})
    with pytest.raises(LlmError, match="does not match the schema"):
        extractor.extract(TEXT)


def test_confidence_outside_0_1_is_rejected():
    with pytest.raises(ValidationError):
        answer(invoice_number={"value": "RE-1", "confidence": 1.5})


def test_default_factory_passes_the_key_from_settings(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.update(kw))
    default_client_factory(settings(tmp_path, anthropic_api_key="sk-test-secret"))
    assert seen == {"api_key": "sk-test-secret"}


def test_empty_key_from_env_example_means_no_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert settings(tmp_path).anthropic_api_key is None
