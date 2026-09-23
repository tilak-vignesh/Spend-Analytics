"""Tools the chat agent can call. Every result is sent to the LLM, so tools only read
the restricted chat views (no balances, bank refs or account details) and `call`
never raises: failures come back as {"error": ...} for the model to react to.
"""

import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.db import readonly_connection
from app.money import format_inr

ALLOWED_SOURCES = {"chat_transactions", "chat_anomalies", "categories"}
ACCESS_ERROR = ("not allowed: chat queries may only read chat_transactions, "
                "chat_anomalies and categories")
_LEADING_COMMENTS = re.compile(r"^\s*(?:(?:--[^\n]*\n)|(?:/\*.*?\*/)|\s)*", re.S)


def _make_authorizer(schema_objects: set[str]):
    """SQLite calls this while compiling each statement. Reads of real tables/views
    must be of an allowed one, or happen inside an allowed view's own definition.
    Names that aren't schema objects are CTEs/subqueries, whose own bodies are
    authorized separately, so reading them is safe."""
    def authorize(action, arg1, arg2, _db, source):
        if action == sqlite3.SQLITE_READ:
            ok = (arg1 in ALLOWED_SOURCES or source in ALLOWED_SOURCES
                  or arg1 not in schema_objects)
            return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def _iso(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a date like 2026-09-01, got {value!r}") from None


SPECS = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQLite SELECT (or WITH ... SELECT) query. Only the views "
            "chat_transactions and chat_anomalies and the table categories can be read. "
            "Amounts are integer paise (divide by 100 for rupees); debits are negative. "
            "At most 200 rows are returned."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The SQL query"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_category_breakdown",
        "description": (
            "Spend per category between two dates (inclusive). Spend = debits, excluding "
            "transfers between the user's own accounts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_anomalies",
        "description": (
            "Flagged unusual spending (duplicates, new merchants, price changes, category "
            "spikes, unusually large payments), optionally within a date range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                "status": {"type": "string", "enum": ["open", "dismissed", "all"]},
            },
        },
    },
]


class ChatTools:
    specs = SPECS

    def __init__(self, db_path: str | Path, max_rows: int = 200, timeout_ms: int = 2000):
        self.db_path = db_path
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms

    def call(self, name: str, args: dict) -> dict:
        handler = {
            "run_sql": self.run_sql,
            "get_category_breakdown": self.get_category_breakdown,
            "get_anomalies": self.get_anomalies,
        }.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(**(args or {}))
        except TypeError as exc:  # missing / unexpected arguments
            return {"error": f"bad arguments for {name}: {exc}"}
        except ValueError as exc:
            return {"error": str(exc)}

    def _connect(self) -> sqlite3.Connection:
        conn = readonly_connection(self.db_path)
        schema_objects = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master UNION SELECT 'sqlite_master' "
            "UNION SELECT 'sqlite_schema'")}
        conn.set_authorizer(_make_authorizer(schema_objects))
        deadline = time.monotonic() + self.timeout_ms / 1000
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        return conn

    def _query(self, sql: str, params: tuple = ()) -> tuple[list[str], list[list], bool]:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchmany(self.max_rows + 1)
            columns = [d[0] for d in cursor.description or []]
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "prohibited" in message or "not authorized" in message:
                raise ValueError(ACCESS_ERROR) from None
            if "interrupted" in message:
                raise ValueError(f"query took too long (limit {self.timeout_ms} ms)") from None
            raise ValueError(f"SQL error: {message}") from None
        finally:
            conn.close()
        truncated = len(rows) > self.max_rows
        return columns, [list(r) for r in rows[: self.max_rows]], truncated

    def run_sql(self, query: str) -> dict:
        body = _LEADING_COMMENTS.sub("", query or "")
        if not re.match(r"(select|with)\b", body, re.I):
            raise ValueError("only a single SELECT (or WITH ... SELECT) query is allowed")
        columns, rows, truncated = self._query(query)
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}

    def get_category_breakdown(self, start_date: str, end_date: str) -> dict:
        start, end = _iso(start_date, "start_date"), _iso(end_date, "end_date")
        _, rows, _ = self._query("""
            SELECT category, SUM(-amount_paise) AS spend, COUNT(*) AS n
            FROM chat_transactions
            WHERE amount_paise < 0 AND txn_type IS NOT 'transfer'
              AND txn_date >= ? AND txn_date < ?
            GROUP BY category ORDER BY spend DESC, category
        """, (start.isoformat(), (end + timedelta(days=1)).isoformat()))
        total = sum(r[1] for r in rows)
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "total_spend_paise": total,
            "total_spend": format_inr(total),
            "categories": [
                {"category": cat or "Uncategorized", "spend_paise": spend,
                 "spend": format_inr(spend), "count": n}
                for cat, spend, n in rows
            ],
        }

    def get_anomalies(self, start_date: str | None = None, end_date: str | None = None,
                      status: str = "open") -> dict:
        if status not in ("open", "dismissed", "all"):
            raise ValueError("status must be open, dismissed or all")
        start = _iso(start_date, "start_date").isoformat() if start_date else "0000-01-01"
        end = _iso(end_date, "end_date").isoformat() if end_date else "9999-12-31"
        _, rows, _ = self._query("""
            SELECT a.id, a.anomaly_type, a.status, COALESCE(t.txn_date, a.month) AS d,
                   a.category, a.note
            FROM chat_anomalies a LEFT JOIN chat_transactions t ON t.id = a.transaction_id
            WHERE (? = 'all' OR a.status = ?)
              AND COALESCE(t.txn_date, a.month || '-01') BETWEEN ? AND ?
            ORDER BY d DESC, a.id DESC
        """, (status, status, start, end))
        return {"anomalies": [
            {"id": id, "type": kind, "status": st, "date": d, "category": cat, "note": note}
            for id, kind, st, d, cat, note in rows
        ]}
