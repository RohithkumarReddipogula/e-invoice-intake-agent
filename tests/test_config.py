import pytest
from pydantic import ValidationError

from intake.config import Settings


def make_settings(**kwargs) -> Settings:
    # _env_file=None: tests never read a developer's real .env.
    return Settings(_env_file=None, **kwargs)


def test_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INTAKE_LLM_OFFLINE", raising=False)
    settings = make_settings()
    assert settings.anthropic_api_key is None
    assert settings.llm_offline is False
    assert settings.confidence_threshold == 0.9


def test_api_key_is_not_shown(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    settings = make_settings()
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-test-not-a-real-key"
    assert "sk-test-not-a-real-key" not in repr(settings)
    assert "sk-test-not-a-real-key" not in str(settings.model_dump())


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("INTAKE_LLM_OFFLINE", "1")
    monkeypatch.setenv("INTAKE_LLM_MODEL", "claude-haiku-5-5")
    settings = make_settings()
    assert settings.llm_offline is True
    assert settings.llm_model == "claude-haiku-5-5"


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_threshold_must_be_between_0_and_1(value):
    with pytest.raises(ValidationError):
        make_settings(confidence_threshold=value)
