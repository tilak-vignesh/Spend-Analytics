from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.migrate import migrate

# Lets the opt-in real-statement test find HDFC_PDF_PASSWORD in .env. Loaded at
# import time because skipif conditions are evaluated during collection.
load_dotenv(Path(__file__).parent.parent / ".env")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrate(path)
    return path


class FakeLLM:
    """Stands in for GeminiJSON: names every payee after its narration's first word."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *, system, prompt, schema):
        import json

        self.calls += 1
        batch = json.loads(prompt.split("PAYEES:\n", 1)[1])
        return {"items": [
            dict(id=p["id"], payee_name=None, payee_vpa=None,
                 merchant_normalized=p["narration"].split("-")[0][:20], payee_type="merchant",
                 is_person_name=False, category="Other", confidence=0.5)
            for p in batch
        ]}


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def engine(db_path):
    from app.db import make_engine

    return make_engine(db_path)


@pytest.fixture
def client(engine, db_path, tmp_path, fake_llm, monkeypatch):
    """API client on a temp DB, fake LLM and temp statements dir."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.chat.tools import ChatTools
    from app.db import get_engine, get_session
    from app.llm.gemini import get_llm_provider
    from app.main import app
    from app.routers.chat import get_chat_tools
    from app.routers.statements import get_statements_dir

    monkeypatch.setattr("app.main.DB_PATH", db_path)

    def session_override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides.update({
        get_session: session_override,
        get_engine: lambda: engine,
        get_llm_provider: lambda: (lambda: fake_llm),
        get_statements_dir: lambda: tmp_path / "statements",
        get_chat_tools: lambda: ChatTools(db_path),
    })
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
