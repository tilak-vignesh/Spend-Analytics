"""POST /api/chat: one question in, a grounded answer plus the tool calls behind it."""

from collections.abc import Callable
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session

from app.chat.agent import run_chat
from app.chat.tools import ChatTools
from app.db import DB_PATH, get_session
from app.llm.gemini import get_chat_provider

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=100)


def get_chat_tools() -> ChatTools:
    return ChatTools(DB_PATH)


@router.post("")
def chat(body: ChatRequest,
         session: Session = Depends(get_session),
         tools: ChatTools = Depends(get_chat_tools),
         model_provider: Callable = Depends(get_chat_provider)) -> dict:
    try:
        model = model_provider()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None

    first, last = session.connection().execute(
        text("SELECT MIN(txn_date), MAX(txn_date) FROM transactions")).one()
    data_range = (date.fromisoformat(first) if first else None,
                  date.fromisoformat(last) if last else None)
    try:
        result = run_chat(body.message, [t.model_dump() for t in body.history], model, tools,
                          today=date.today(), data_range=data_range)
    except Exception as exc:  # provider/network failure
        raise HTTPException(502, f"Chat model error: {exc}") from None

    return {
        "answer": result.answer,
        "steps": result.steps,
        "tool_calls": [
            {"name": t.name, "args": t.args, "error": t.result.get("error"),
             "row_count": t.result.get("row_count")}
            for t in result.tool_calls
        ],
    }
