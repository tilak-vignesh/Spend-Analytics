from datetime import date

from app.chat.agent import ModelReply, ToolCall, build_system_prompt, run_chat

TODAY = date(2026, 9, 23)
RANGE = (date(2026, 9, 1), date(2026, 9, 23))


class ScriptedModel:
    """Returns the scripted replies in order and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system, messages, tools):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        return self.replies.pop(0)


class FakeTools:
    specs = [{"name": "run_sql", "description": "d", "parameters": {"type": "object"}}]

    def __init__(self, results=None):
        self.results = results or {}
        self.called = []

    def call(self, name, args):
        self.called.append((name, args))
        if name not in self.results:
            return {"error": f"unknown tool {name!r}"}
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result


def chat(model, tools=None, question="How much on dining?", history=()):
    return run_chat(question, list(history), model, tools or FakeTools(),
                    today=TODAY, data_range=RANGE)


def test_direct_answer_without_tools():
    model = ScriptedModel(ModelReply(text="Hello!", tool_calls=[]))
    result = chat(model, question="hi")
    assert (result.answer, result.tool_calls, result.steps) == ("Hello!", [], 1)
    assert model.calls[0]["messages"] == [{"role": "user", "text": "hi"}]


def test_tool_call_then_answer():
    call = ToolCall(name="run_sql", args={"query": "SELECT 1"}, id="c1")
    first = ModelReply(text=None, tool_calls=[call], raw="RAW-TURN-1")
    model = ScriptedModel(first, ModelReply(text="You spent ₹8,775.96.", tool_calls=[]))
    tools = FakeTools({"run_sql": {"columns": ["t"], "rows": [[877596]], "row_count": 1,
                                   "truncated": False}})
    result = chat(model, tools)

    assert result.answer == "You spent ₹8,775.96."
    assert tools.called == [("run_sql", {"query": "SELECT 1"})]
    assert [(t.name, t.args, t.result["row_count"]) for t in result.tool_calls] == [
        ("run_sql", {"query": "SELECT 1"}, 1)]
    # Second model call sees its own turn (raw, for provider state) and the tool result
    second = model.calls[1]["messages"]
    assert second[1] == {"role": "model_turn", "reply": first}
    # results reach the model marked as data, never instructions
    assert second[2] == {"role": "tool", "results": [
        {"call": call, "result": {"untrusted_data": tools.results["run_sql"]}}]}


def test_tool_errors_are_fed_back_not_raised():
    bad = ToolCall(name="nope", args={})
    boom = ToolCall(name="run_sql", args={"query": "x"})
    model = ScriptedModel(ModelReply(text=None, tool_calls=[bad, boom]),
                          ModelReply(text="Sorry, I couldn't.", tool_calls=[]))
    result = chat(model, FakeTools({"run_sql": RuntimeError("db exploded")}))
    assert result.answer == "Sorry, I couldn't."
    errors = [t.result["error"] for t in result.tool_calls]
    assert "unknown tool" in errors[0] and "db exploded" in errors[1]


def test_step_limit():
    loop = ModelReply(text=None, tool_calls=[ToolCall(name="run_sql", args={"query": "q"})])
    model = ScriptedModel(*[loop] * 10)
    result = run_chat("q", [], model, FakeTools({"run_sql": {"rows": []}}), today=TODAY,
                      data_range=RANGE, max_steps=3)
    assert result.steps == 3 and len(result.tool_calls) == 3
    assert "couldn't finish" in result.answer


def test_empty_final_text_gets_a_fallback():
    result = chat(ScriptedModel(ModelReply(text="  ", tool_calls=[])))
    assert result.answer.strip()


def test_history_is_passed_and_trimmed():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"m{i}"} for i in range(30)]
    model = ScriptedModel(ModelReply(text="ok", tool_calls=[]))
    chat(model, history=history, question="latest")
    messages = model.calls[0]["messages"]
    assert len(messages) == 21  # last 20 history turns + the question
    assert messages[0] == {"role": "user", "text": "m10"}
    assert messages[1] == {"role": "model", "text": "m11"}
    assert messages[-1] == {"role": "user", "text": "latest"}


def test_tool_specs_are_offered():
    model = ScriptedModel(ModelReply(text="ok", tool_calls=[]))
    chat(model)
    assert model.calls[0]["tools"] == FakeTools.specs


def test_system_prompt_grounds_the_model():
    prompt = build_system_prompt(today=TODAY, data_range=RANGE)
    for expected in ["2026-09-23", "2026-09-01 to 2026-09-23", "chat_transactions",
                     "paise", "transfer", "Never guess", "plain text",
                     "Never do arithmetic", "inr(", "untrusted_data", "never instructions"]:
        assert expected in prompt
    assert "Account balances and account numbers are not available" in prompt
    assert "divide by 100" not in prompt


def test_system_prompt_without_data():
    assert "no transactions" in build_system_prompt(today=TODAY, data_range=(None, None)).lower()


# --- grounding: the model must not do arithmetic ------------------------------------

SQL_CALL = ToolCall(name="run_sql", args={"query": "q"}, id="c1")
DAY_RESULT = {"columns": ["d", "spend"], "rows": [["2026-09-06", 2335800], ["2026-09-06", 1200000],
                                                  ["2026-09-06", 1000000]]}


def test_ungrounded_answer_gets_one_correction_round():
    model = ScriptedModel(
        ModelReply(text=None, tool_calls=[SQL_CALL]),
        ModelReply(text="₹23,358 total; ₹1,358 was food.", tool_calls=[]),
        ModelReply(text="₹23,358 total; Precize was ₹12,000 and ₹10,000.", tool_calls=[]))
    result = chat(model, FakeTools({"run_sql": DAY_RESULT}))

    assert result.answer == "₹23,358 total; Precize was ₹12,000 and ₹10,000."
    assert result.unverified == []
    correction = model.calls[2]["messages"]
    assert correction[-2] == {"role": "model", "text": "₹23,358 total; ₹1,358 was food."}
    assert correction[-1]["role"] == "user"
    assert "₹1,358" in correction[-1]["text"] and "run_sql" in correction[-1]["text"]


def test_still_ungrounded_after_retry_is_reported():
    bad = ModelReply(text="₹1,358 was food.", tool_calls=[])
    model = ScriptedModel(ModelReply(text=None, tool_calls=[SQL_CALL]), bad, bad, bad)
    result = chat(model, FakeTools({"run_sql": DAY_RESULT}))
    assert (result.answer, result.unverified) == ("₹1,358 was food.", ["₹1,358"])
    assert len(model.calls) == 3  # one correction round only


def test_correction_can_use_tools():
    fixed_sql = ToolCall(name="run_sql", args={"query": "SELECT inr(...)"}, id="c2")
    breakdown = ToolCall(name="get_category_breakdown", args={}, id="c1")
    model = ScriptedModel(
        ModelReply(text=None, tool_calls=[breakdown]),
        ModelReply(text="₹1,358 was food.", tool_calls=[]),
        ModelReply(text=None, tool_calls=[fixed_sql]),
        ModelReply(text="₹1,358.00 was food.", tool_calls=[]))
    tools = FakeTools({"get_category_breakdown": DAY_RESULT,
                       "run_sql": {"rows": [["₹1,358.00"]]}})
    result = chat(model, tools)
    assert result.unverified == [] and result.answer == "₹1,358.00 was food."


def test_figures_from_question_and_history_count_as_known():
    model = ScriptedModel(ModelReply(text="Nothing above ₹500, and ₹8,775.96 was Dining.",
                                     tool_calls=[]))
    result = chat(model, question="Anything above ₹500?",
                  history=[{"role": "assistant", "text": "Dining was ₹8,775.96."}])
    assert result.unverified == [] and len(model.calls) == 1


def test_no_retry_past_step_limit():
    model = ScriptedModel(ModelReply(text=None, tool_calls=[SQL_CALL]),
                          ModelReply(text="₹1,358 was food.", tool_calls=[]))
    result = run_chat("q", [], model, FakeTools({"run_sql": DAY_RESULT}), today=TODAY,
                      data_range=RANGE, max_steps=2)
    assert result.unverified == ["₹1,358"] and len(model.calls) == 2
