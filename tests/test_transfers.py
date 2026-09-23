from datetime import date

import pytest
from sqlmodel import Session, select

from app.categorization.transfers import mark_transfers
from app.db import make_engine
from app.models import Account, Transaction


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        s.add(Account(id=2, name="Credit Card 9999"))
        s.commit()
    return engine


def add(engine, id, account_id, day, amount, **kw):
    with Session(engine) as s:
        s.add(Transaction(id=id, account_id=account_id, txn_date=date(2026, 9, day),
                          amount_paise=amount, dedupe_key=f"k{id}", narration=f"N{id}",
                          txn_type="debit" if amount < 0 else "credit", **kw))
        s.commit()


def txns(engine):
    with Session(engine) as s:
        return {t.id: t for t in s.exec(select(Transaction)).all()}


def test_pairs_opposite_amounts_across_accounts(engine):
    add(engine, 1, 1, 5, -2500000)  # savings pays card bill
    add(engine, 2, 2, 6, 2500000)  # card receives it
    assert mark_transfers(engine) == 1
    for t in txns(engine).values():
        assert (t.txn_type, t.category, t.category_source) == ("transfer", "Transfers", "rule")


@pytest.mark.parametrize(
    "credit_account, credit_day, credit_amount",
    [(1, 6, 2500000),  # same account: a refund, not a transfer
     (2, 9, 2500000),  # 4 days apart
     (2, 6, 2400000)],  # different amount
)
def test_non_matches(engine, credit_account, credit_day, credit_amount):
    add(engine, 1, 1, 5, -2500000)
    add(engine, 2, credit_account, credit_day, credit_amount)
    assert mark_transfers(engine) == 0
    assert {t.txn_type for t in txns(engine).values()} == {"debit", "credit"}


def test_each_credit_pairs_once_with_closest_debit(engine):
    add(engine, 1, 1, 2, -100000)
    add(engine, 2, 1, 5, -100000)
    add(engine, 3, 2, 5, 100000)
    assert mark_transfers(engine) == 1
    t = txns(engine)
    assert (t[1].txn_type, t[2].txn_type, t[3].txn_type) == ("debit", "transfer", "transfer")


def test_manual_categories_are_left_alone(engine):
    add(engine, 1, 1, 5, -2500000, category="Rent", category_source="manual")
    add(engine, 2, 2, 5, 2500000)
    assert mark_transfers(engine) == 0


def test_idempotent(engine):
    add(engine, 1, 1, 5, -2500000)
    add(engine, 2, 2, 5, 2500000)
    mark_transfers(engine)
    assert mark_transfers(engine) == 0
