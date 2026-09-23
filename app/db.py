"""SQLite engine/session setup.

The DB path comes from TXN_DB_PATH (default: data/transactions.db in the repo root).
"""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("TXN_DB_PATH", REPO_ROOT / "data" / "transactions.db"))


def make_engine(db_path: str | Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    return engine


engine = make_engine(DB_PATH)


def get_engine() -> Engine:
    """FastAPI dependency, for code that manages its own sessions."""
    return engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as session:
        yield session


def readonly_connection(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Connection that SQLite itself refuses to write through. Used by chat run_sql."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn
