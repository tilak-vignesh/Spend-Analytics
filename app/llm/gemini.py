"""Gemini adapter: a `generate_json(system=, prompt=, schema=)` callable.

Config from env: GEMINI_API_KEY (required), GEMINI_MODEL (optional).
"""

import json
import os
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

from app.chat.agent import ModelReply, ToolCall

# Pinned rather than an alias like gemini-flash-latest, so categorization doesn't
# change underneath us when Google moves the alias.
DEFAULT_MODEL = "gemini-3.8-flash"


def _client_and_model_from_env():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=120_000,  # ms
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
    )
    return client, os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


class GeminiJSON:
    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    @classmethod
    def from_env(cls) -> "GeminiJSON":
        return cls(*_client_and_model_from_env())

    def __call__(self, *, system: str, prompt: str, schema: dict) -> Any:
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema,
                temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        return json.loads(response.text)


def get_llm_provider() -> Callable[[], GeminiJSON]:
    """FastAPI dependency. Returns a factory rather than a client so endpoints can
    report a missing key without failing the whole request."""
    return GeminiJSON.from_env


class GeminiChat:
    """Chat model for app.chat.agent: `(system, messages, tool_specs) -> ModelReply`,
    using Gemini function calling. The model's own turns are replayed verbatim (raw
    Content) so thought signatures survive multi-step tool use."""

    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    @classmethod
    def from_env(cls) -> "GeminiChat":
        return cls(*_client_and_model_from_env())

    @staticmethod
    def _contents(messages: list[dict]) -> list:
        contents = []
        for m in messages:
            role = m["role"]
            if role in ("user", "model"):
                contents.append(types.Content(role=role, parts=[types.Part(text=m["text"])]))
            elif role == "model_turn":
                reply = m["reply"]
                contents.append(reply.raw if reply.raw is not None else types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(
                        id=c.id, name=c.name, args=c.args)) for c in reply.tool_calls],
                ))
            elif role == "tool":
                contents.append(types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        id=r["call"].id, name=r["call"].name, response=r["result"]))
                    for r in m["results"]
                ]))
        return contents

    def __call__(self, system: str, messages: list[dict], tool_specs: list[dict]) -> ModelReply:
        response = self._client.models.generate_content(
            model=self.model,
            contents=self._contents(messages),
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=[
                    types.FunctionDeclaration(name=s["name"], description=s["description"],
                                              parameters_json_schema=s["parameters"])
                    for s in tool_specs
                ])],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        content = response.candidates[0].content if response.candidates else None
        parts = (content.parts or []) if content else []
        calls = [ToolCall(name=p.function_call.name, args=dict(p.function_call.args or {}),
                          id=p.function_call.id)
                 for p in parts if p.function_call]
        text = "".join(p.text for p in parts if p.text and not p.thought) or None
        return ModelReply(text=text, tool_calls=calls, raw=content)


def get_chat_provider() -> Callable[[], GeminiChat]:
    """FastAPI dependency (a factory, so a missing key becomes a clean error)."""
    return GeminiChat.from_env
