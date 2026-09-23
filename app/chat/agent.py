"""Tool-calling chat loop, independent of the LLM provider.

The model is any callable `(system, messages, tool_specs) -> ModelReply`. Messages:
  {"role": "user" | "model", "text": ...}      plain turns (history, the question)
  {"role": "model_turn", "reply": ModelReply}  the model's tool-calling turn, kept
                                               whole so providers can replay it
  {"role": "tool", "results": [{"call": ToolCall, "result": dict}]}
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

MAX_HISTORY_TURNS = 20


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict
    id: str | None = None


@dataclass(frozen=True)
class ModelReply:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any = None  # provider-native turn (e.g. Gemini Content with thought signatures)


@dataclass(frozen=True)
class ToolTrace:
    name: str
    args: dict
    result: dict


@dataclass
class ChatResult:
    answer: str
    tool_calls: list[ToolTrace] = field(default_factory=list)
    steps: int = 0


ChatModel = Callable[[str, list, list[dict]], ModelReply]


def build_system_prompt(today: date, data_range: tuple[date | None, date | None]) -> str:
    first, last = data_range
    coverage = (f"Transactions on record cover {first} to {last}."
                if first else "There are no transactions on record yet.")
    return f"""\
You answer questions about the user's personal spending from their HDFC bank
statements (India, amounts in rupees). Today is {today}. {coverage}

Data you can query with run_sql (SQLite, read-only):
- chat_transactions(id, txn_date 'YYYY-MM-DD', amount_paise, payee, payee_type,
  category, category_source, channel, txn_type, narration)
  * amount_paise is an integer in paise: divide by 100 for rupees. Debits (spend) are
    negative, credits (money received) positive.
  * txn_type is debit / credit / transfer. Transfers are money moved between the user's
    own accounts: always exclude them from spend and income (txn_type IS NOT 'transfer').
  * payee is the cleaned payee name; payee_type is merchant / merchant_qr / person.
  * category_source: llm (AI-assigned), p2p_default (payment to a person, defaulted to
    Food & Groceries), rule (user-confirmed payee), manual (set by the user).
  * narration is the raw bank text; "\\n" marks a line wrap.
- chat_anomalies(id, anomaly_type, status, transaction_id, category, month, note)
- categories(name)
Account balances and account numbers are not available; say so if asked.

Rules:
- Never guess numbers. Every figure in your answer must come from a tool result in
  this conversation. If the data doesn't cover the question, say so.
- Prefer get_category_breakdown / get_anomalies when they fit; use run_sql otherwise.
  Aggregate in SQL (SUM, COUNT, GROUP BY) rather than fetching many rows.
- Resolve relative dates ("last month", "this week") against today's date and the data
  coverage above, and state the period you used.
- Write rupee amounts like ₹1,23,456.78 (Indian digit grouping).
- Answer in plain text only (no markdown, no tables), concisely.
"""


def _history_messages(history: list[dict]) -> list[dict]:
    turns = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        text = str(turn.get("text", "")).strip()
        if text:
            turns.append({"role": "user" if turn.get("role") == "user" else "model", "text": text})
    return turns


def run_chat(question: str, history: list[dict], model: ChatModel, tools, *,
             today: date, data_range: tuple[date | None, date | None],
             max_steps: int = 8) -> ChatResult:
    system = build_system_prompt(today, data_range)
    messages = _history_messages(history) + [{"role": "user", "text": question}]
    result = ChatResult(answer="")

    while result.steps < max_steps:
        reply = model(system, messages, tools.specs)
        result.steps += 1
        if not reply.tool_calls:
            result.answer = (reply.text or "").strip() or "I don't have an answer for that."
            return result

        messages.append({"role": "model_turn", "reply": reply})
        tool_results = []
        for call in reply.tool_calls:
            try:
                output = tools.call(call.name, call.args)
            except Exception as exc:  # a tool bug shouldn't kill the conversation
                output = {"error": f"tool failed: {exc}"}
            tool_results.append({"call": call, "result": output})
            result.tool_calls.append(ToolTrace(call.name, call.args, output))
        messages.append({"role": "tool", "results": tool_results})

    result.answer = ("I couldn't finish answering that within the step limit. "
                     "Try asking a narrower question.")
    return result
