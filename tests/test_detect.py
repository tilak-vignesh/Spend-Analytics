import json
from datetime import date

import pytest
from sqlmodel import Session, select

from app.anomalies.detect import detect_anomalies
from app.db import make_engine
from app.models import Account, Anomaly, Transaction


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        s.commit()
    return engine


def add(engine, id, day, amount, key="vpa:vpn@ybl", name="ExampleVPN", txn_type=None,
        category="Software & Hosting"):
    with Session(engine) as s:
        s.add(Transaction(id=id, account_id=1, txn_date=date(2026, 9, day), amount_paise=amount,
                          dedupe_key=f"k{id}", narration="N", merchant_key=key,
                          merchant_normalized=name, category=category, channel="upi",
                          txn_type=txn_type or ("debit" if amount < 0 else "credit")))
        s.commit()


def anomalies(engine):
    with Session(engine) as s:
        return s.exec(select(Anomaly).order_by(Anomaly.id)).all()


def test_stores_findings(engine):
    add(engine, 1, 1, -146413)
    add(engine, 2, 1, -146413)
    result = detect_anomalies(engine)

    [a] = anomalies(engine)
    assert (a.anomaly_type, a.transaction_id, a.related_transaction_id, a.status) == (
        "duplicate", 2, 1, "open")
    assert a.category == "Software & Hosting"
    assert json.loads(a.details)["spend_paise"] == 146413
    assert a.note.startswith("Possible duplicate: ₹1,464.13 to ExampleVPN")
    assert (result.new, result.updated, result.removed, result.open_by_type) == (
        1, 0, 0, {"duplicate": 1})


def test_credits_and_transfers_are_not_spend(engine):
    add(engine, 1, 1, 146413)
    add(engine, 2, 1, 146413)
    add(engine, 3, 1, -146413, txn_type="transfer")
    add(engine, 4, 1, -146413, txn_type="transfer")
    detect_anomalies(engine)
    assert anomalies(engine) == []


def test_rerun_is_idempotent(engine):
    add(engine, 1, 1, -146413)
    add(engine, 2, 1, -146413)
    detect_anomalies(engine)
    result = detect_anomalies(engine)
    assert len(anomalies(engine)) == 1
    assert (result.new, result.updated, result.removed) == (0, 0, 0)


def test_changed_finding_is_updated(engine):
    add(engine, 1, 1, -146413)
    add(engine, 2, 1, -146413)
    detect_anomalies(engine)
    with Session(engine) as s:
        for t in s.exec(select(Transaction)).all():
            t.merchant_normalized = "Example VPN Ltd"
            s.add(t)
        s.commit()
    result = detect_anomalies(engine)
    assert result.updated == 1
    assert "Example VPN Ltd" in anomalies(engine)[0].note


def test_resolved_open_findings_are_removed_but_dismissed_kept(engine):
    add(engine, 1, 1, -146413)
    add(engine, 2, 1, -146413)
    add(engine, 3, 5, -90000, key="vpa:shop@ybl", name="Shop", category="Shopping")
    add(engine, 4, 5, -90000, key="vpa:shop@ybl", name="Shop", category="Shopping")
    detect_anomalies(engine)
    with Session(engine) as s:
        shop = s.exec(select(Anomaly).where(Anomaly.transaction_id == 4)).one()
        shop.status = "dismissed"
        s.add(shop)
        for t in s.exec(select(Transaction).where(Transaction.id.in_([2, 4]))).all():
            t.amount_paise = -100  # no longer duplicates
            s.add(t)
        s.commit()

    result = detect_anomalies(engine)
    assert result.removed == 1
    assert [(a.transaction_id, a.status) for a in anomalies(engine)] == [(4, "dismissed")]


def test_empty_database(engine):
    result = detect_anomalies(engine)
    assert (result.new, result.open_by_type) == (0, {})
