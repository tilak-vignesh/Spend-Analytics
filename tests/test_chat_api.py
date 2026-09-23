from datetime import date

import pytest
from sqlmodel import Session

from app.chat.agent import ModelReply, ToolCall
from app.llm.gemini import get_chat_provider
from app.main import app
from app.models import Account, Transaction


class ScriptedModel:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, system, messages, tools):
        self.calls.append({"system": system, "messages": messages})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def engine(engine):
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        s.add(Transaction(id=1, account_id=1, txn_date=date(2026, 9, 5), amount_paise=-30000,
                          dedupe_key="k1", narration="N", category="Dining",
                          merchant_normalized="District", txn_type="debit"))
        s.commit()
    return engine


def use_model(model):
    app.dependency_overrides[get_chat_provider] = lambda: (lambda: model)


def test_chat_runs_tools_against_the_database(client):
    sql = "SELECT SUM(-amount_paise) FROM chat_transactions WHERE category = 'Dining'"
    model = ScriptedModel(
        ModelReply(text=None, tool_calls=[ToolCall(name="run_sql", args={"query": sql}, id="c1")]),
        ModelReply(text="You spent ₹300.00 on Dining.", tool_calls=[]))
    use_model(model)

    r = client.post("/api/chat", json={"message": "Dining spend?"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "answer": "You spent ₹300.00 on Dining.",
        "steps": 2,
        "tool_calls": [{"name": "run_sql", "args": {"query": sql}, "error": None, "row_count": 1}],
    }
    # the real tool ran on the temp DB and its rows went back to the model
    tool_msg = model.calls[1]["messages"][-1]
    assert tool_msg["results"][0]["result"]["rows"] == [[30000]]
    assert "2026-09-05 to 2026-09-05" in model.calls[0]["system"]


def test_history_is_forwarded(client):
    model = ScriptedModel(ModelReply(text="ok", tool_calls=[]))
    use_model(model)
    client.post("/api/chat", json={"message": "and August?", "history": [
        {"role": "user", "text": "Dining in September?"},
        {"role": "assistant", "text": "₹300.00"}]})
    assert [m["role"] for m in model.calls[0]["messages"]] == ["user", "model", "user"]


def test_tool_errors_are_reported(client):
    use_model(ScriptedModel(
        ModelReply(text=None, tool_calls=[ToolCall(name="run_sql",
                                                   args={"query": "SELECT * FROM accounts"})]),
        ModelReply(text="I can't see account details.", tool_calls=[])))
    [call] = client.post("/api/chat", json={"message": "account?"}).json()["tool_calls"]
    assert "not allowed" in call["error"] and call["row_count"] is None


def test_validation(client):
    use_model(ScriptedModel())
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 5000}).status_code == 422
    assert client.post("/api/chat", json={"message": "q",
                                          "history": [{"role": "system", "text": "x"}]}).status_code == 422


def test_missing_api_key(client):
    def no_key():
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")

    app.dependency_overrides[get_chat_provider] = lambda: no_key
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


def test_model_failure(client):
    use_model(ScriptedModel(ConnectionError("503 overloaded")))
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502 and "503 overloaded" in r.json()["detail"]
