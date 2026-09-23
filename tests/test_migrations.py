import sqlite3
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine, readonly_connection
from app.migrate import list_migrations, migrate
from app.models import Account, Category, Transaction


def test_migrate_sets_latest_version(db_path):
    latest = list_migrations()[-1][0]
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest


def test_migrate_is_idempotent(db_path):
    assert migrate(db_path) == list_migrations()[-1][0]
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 18


def test_indexes_exist(db_path):
    conn = sqlite3.connect(db_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_txn_date", "idx_txn_category", "idx_txn_account", "idx_txn_merchant_key"} <= names


def test_models_match_schema(db_path):
    conn = sqlite3.connect(db_path)
    for table in SQLModel.metadata.sorted_tables:
        db_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table.name})")}
        assert db_cols, f"table {table.name} missing from migrations"
        assert db_cols == {c.name for c in table.columns}, table.name


@pytest.fixture
def session(db_path):
    with Session(make_engine(db_path)) as session:
        yield session


def _txn(**overrides):
    fields = dict(
        account_id=1,
        txn_date=date(2026, 9, 1),
        amount_paise=-146413,
        dedupe_key="1:0000624408798431",
        narration="POS 416021XXXXXX1596 624408798431 01SEP26 14:26:06 EXPRESSVPN.COM",
    )
    fields.update(overrides)
    return Transaction(**fields)


def test_orm_round_trip(session):
    session.add(Account(id=1, name="HDFC Savings", account_type="savings"))
    session.add(_txn(category="Software & Hosting"))
    session.commit()

    txn = session.exec(select(Transaction)).one()
    assert txn.amount_paise == -146413
    assert txn.txn_date == date(2026, 9, 1)
    assert txn.created_at is not None
    assert session.get(Category, "Software & Hosting") is not None


def test_dedupe_key_is_unique(session):
    session.add(Account(id=1, name="HDFC Savings"))
    session.add(_txn())
    session.commit()
    session.add(_txn())
    with pytest.raises(Exception, match="UNIQUE"):
        session.commit()


def test_foreign_keys_enforced(session):
    session.add(_txn(account_id=999))
    with pytest.raises(Exception, match="FOREIGN KEY"):
        session.commit()


def test_unknown_category_rejected(session):
    session.add(Account(id=1, name="HDFC Savings"))
    session.add(_txn(category="Made Up Category"))
    with pytest.raises(Exception, match="FOREIGN KEY"):
        session.commit()


def test_check_constraint_rejects_bad_enum(session):
    session.add(Account(id=1, name="HDFC Savings"))
    session.add(_txn(payee_type="restaurant"))
    with pytest.raises(Exception, match="CHECK"):
        session.commit()


def test_readonly_connection_refuses_writes(db_path):
    conn = readonly_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 18
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM categories")


def _view_columns(db_path, view):
    conn = sqlite3.connect(db_path)
    return [r[1] for r in conn.execute(f"PRAGMA table_info({view})")]


def test_chat_views_expose_only_safe_columns(db_path):
    assert _view_columns(db_path, "chat_transactions") == [
        "id", "txn_date", "amount_paise", "payee", "payee_type", "category",
        "category_source", "channel", "txn_type", "narration",
    ]
    assert _view_columns(db_path, "chat_anomalies") == [
        "id", "anomaly_type", "status", "transaction_id", "category", "month", "note",
    ]
    # Never reachable from chat: balances, bank refs, account names, dedupe keys
    exposed = set(_view_columns(db_path, "chat_transactions"))
    assert not exposed & {"balance_after_paise", "ref_no", "dedupe_key", "account_id",
                          "source_file", "payee_vpa", "payee_ifsc"}
