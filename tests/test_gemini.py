from types import SimpleNamespace

import pytest

from app.llm.gemini import DEFAULT_MODEL, GeminiJSON


class FakeModels:
    def __init__(self, text):
        self.text, self.calls = text, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text)


def test_sends_structured_request_and_parses_json():
    models = FakeModels('{"items": [{"id": 0}]}')
    llm = GeminiJSON(SimpleNamespace(models=models), model="gemini-test")
    schema = {"type": "object"}

    assert llm(system="SYS", prompt="PROMPT", schema=schema) == {"items": [{"id": 0}]}
    [call] = models.calls
    assert call["model"] == "gemini-test"
    assert call["contents"] == "PROMPT"
    config = call["config"]
    assert config.system_instruction == "SYS"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
    assert config.temperature == 0
    assert config.automatic_function_calling.disable is True  # no tools; silences SDK warning


def test_invalid_json_raises():
    llm = GeminiJSON(SimpleNamespace(models=FakeModels("not json")), model="m")
    with pytest.raises(ValueError):
        llm(system="", prompt="", schema={})


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiJSON.from_env()


def test_from_env_model_default_and_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert GeminiJSON.from_env().model == DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_MODEL", "gemini-other")
    assert GeminiJSON.from_env().model == "gemini-other"
