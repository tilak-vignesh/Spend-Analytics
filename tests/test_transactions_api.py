from datetime import date

import pytest
from sqlmodel import Session, select

from app.db import make_engine
from app.models import Account, MerchantCategoryMap, Transaction

KEY = "vpa:9000000000@ybl"


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        s.add(MerchantCategoryMap(merchant_key=KEY, category="Other", source="llm",
                                  payee_type="person", is_person_name=1, confidence=0.8))
        for i, (amount, extra) in enumerate([
            (-17500, dict(category="Food & Groceries", category_source="p2p_default")),
            (-1500000, dict(category="Food & Groceries", category_source="p2p_default")),
            (-2000, dict(category="Health", category_source="manual")),
            (-9000, dict(category="Transfers", category_source="rule", txn_type="transfer")),
        ], start=1):
            s.add(Transaction(id=i, account_id=1, txn_date=date(2026, 9, i), amount_paise=amount,
                              dedupe_key=f"k{i}", narration="N", merchant_key=KEY,
                              **{"txn_type": "debit", **extra}))
        s.commit()
    return engine


def txn(engine, id):
    with Session(engine) as s:
        return s.get(Transaction, id)


def test_override_single_transaction(client, engine):
    r = client.patch("/api/transactions/2", json={"category": "Rent"})
    assert r.status_code == 200
    assert r.json() == {"id": 2, "category": "Rent", "category_source": "manual",
                        "other_transactions_updated": 0}
    assert txn(engine, 1).category == "Food & Groceries"  # same payee, untouched
    with Session(engine) as s:
        assert s.get(MerchantCategoryMap, KEY).source == "llm"


def test_apply_to_payee_confirms_cache_and_updates_non_manual_siblings(client, engine):
    r = client.patch("/api/transactions/2", json={"category": "Rent", "apply_to_payee": True})
    assert r.json()["other_transactions_updated"] == 1

    assert (txn(engine, 1).category, txn(engine, 1).category_source) == ("Rent", "rule")
    assert (txn(engine, 3).category, txn(engine, 3).category_source) == ("Health", "manual")
    assert txn(engine, 4).category == "Transfers"  # transfers are left alone
    with Session(engine) as s:
        entry = s.get(MerchantCategoryMap, KEY)
    assert (entry.category, entry.source, entry.confidence) == ("Rent", "manual", 1.0)


def test_unknown_category_rejected(client):
    r = client.patch("/api/transactions/1", json={"category": "Made Up"})
    assert r.status_code == 422
    assert "Made Up" in r.json()["detail"]


def test_missing_transaction(client):
    assert client.patch("/api/transactions/999", json={"category": "Rent"}).status_code == 404


def test_apply_to_payee_needs_a_payee(client, engine):
    with Session(engine) as s:
        t = s.get(Transaction, 1)
        t.merchant_key = None
        s.add(t)
        s.commit()
    r = client.patch("/api/transactions/1", json={"category": "Rent", "apply_to_payee": True})
    assert r.status_code == 409
