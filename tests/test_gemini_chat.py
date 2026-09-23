from types import SimpleNamespace

import pytest
from google.genai import types

from app.chat.agent import ModelReply, ToolCall
from app.llm.gemini import GeminiChat

SPECS = [{"name": "run_sql", "description": "Run SQL",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                         "required": ["query"]}}]


def response(*parts):
    return types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=list(parts)))])


class FakeModels:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.resp


def make(resp):
    models = FakeModels(resp)
    return GeminiChat(SimpleNamespace(models=models), model="gemini-test"), models


def test_request_shape():
    chat, models = make(response(types.Part(text="hi")))
    chat("SYSTEM", [{"role": "user", "text": "q1"}, {"role": "model", "text": "a1"},
                    {"role": "user", "text": "q2"}], SPECS)
    [call] = models.calls
    assert call["model"] == "gemini-test"
    assert [(c.role, c.parts[0].text) for c in call["contents"]] == [
        ("user", "q1"), ("model", "a1"), ("user", "q2")]
    config = call["config"]
    assert config.system_instruction == "SYSTEM"
    assert config.automatic_function_calling.disable is True
    [decl] = config.tools[0].function_declarations
    assert (decl.name, decl.description) == ("run_sql", "Run SQL")
    assert decl.parameters_json_schema == SPECS[0]["parameters"]


def test_parses_function_calls_and_keeps_raw_turn():
    content_part = types.Part(function_call=types.FunctionCall(
        id="call-1", name="run_sql", args={"query": "SELECT 1"}))
    chat, _ = make(response(content_part))
    reply = chat("S", [{"role": "user", "text": "q"}], SPECS)
    assert reply.tool_calls == [ToolCall(name="run_sql", args={"query": "SELECT 1"}, id="call-1")]
    assert reply.text is None
    assert reply.raw.parts[0] is content_part


def test_text_ignores_thought_parts():
    chat, _ = make(response(types.Part(text="thinking...", thought=True),
                            types.Part(text="You spent "), types.Part(text="₹10.")))
    reply = chat("S", [{"role": "user", "text": "q"}], SPECS)
    assert (reply.text, reply.tool_calls) == ("You spent ₹10.", [])


def test_replays_model_turn_and_sends_tool_results():
    raw = types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(id="call-1", name="run_sql", args={"query": "q"}),
        thought_signature=b"sig")])
    call = ToolCall(name="run_sql", args={"query": "q"}, id="call-1")
    chat, models = make(response(types.Part(text="done")))
    chat("S", [
        {"role": "user", "text": "q"},
        {"role": "model_turn", "reply": ModelReply(text=None, tool_calls=[call], raw=raw)},
        {"role": "tool", "results": [{"call": call, "result": {"rows": [[1]]}}]},
    ], SPECS)
    contents = models.calls[0]["contents"]
    assert contents[1] is raw  # thought signature preserved
    fr = contents[2].parts[0].function_response
    assert (contents[2].role, fr.name, fr.id, fr.response) == (
        "user", "run_sql", "call-1", {"rows": [[1]]})


def test_model_turn_without_raw_is_rebuilt():
    call = ToolCall(name="run_sql", args={"query": "q"}, id="c")
    chat, models = make(response(types.Part(text="ok")))
    chat("S", [{"role": "model_turn", "reply": ModelReply(text=None, tool_calls=[call])}], SPECS)
    fc = models.calls[0]["contents"][0].parts[0].function_call
    assert (fc.name, fc.args, fc.id) == ("run_sql", {"query": "q"}, "c")


def test_no_candidates():
    chat, _ = make(types.GenerateContentResponse(candidates=[]))
    reply = chat("S", [{"role": "user", "text": "q"}], SPECS)
    assert reply.tool_calls == [] and reply.text is None


def test_from_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiChat.from_env()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-x")
    assert GeminiChat.from_env().model == "gemini-x"
