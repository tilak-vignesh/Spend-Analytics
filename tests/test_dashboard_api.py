from datetime import date

import pytest
from sqlmodel import Session

from app.models import Account, Anomaly, Transaction

# id, date, amount, category, merchant, txn_type
ROWS = [
    (1, date(2026, 8, 5), -50000, "Dining", "Swiggy", "debit"),
    (2, date(2026, 8, 10), -20000, "Food & Groceries", "Laxmi Store", "debit"),
    (3, date(2026, 8, 15), 500000, "Income", "Acme Payroll", "credit"),
    (4, date(2026, 9, 1), -146413, "Software & Hosting", "ExpressVPN", "debit"),
    (5, date(2026, 9, 2), -5740, "Health", "Apollo Pharmacy", "debit"),
    (6, date(2026, 9, 2), -17500, "Food & Groceries", "Ravi Kumar", "debit"),
    (7, date(2026, 9, 3), -2500000, "Transfers", "Own Card", "transfer"),
    (8, date(2026, 9, 4), 600000, "Income", "Acme Payroll", "credit"),
    (9, date(2026, 9, 5), -30000, None, None, "debit"),
]


@pytest.fixture
def engine(engine):
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        for id, d, amount, cat, merchant, txn_type in ROWS:
            s.add(Transaction(id=id, account_id=1, txn_date=d, amount_paise=amount,
                              dedupe_key=f"k{id}", narration=f"NARRATION {merchant}",
                              category=cat, merchant_normalized=merchant, txn_type=txn_type,
                              category_source="llm" if cat else None, channel="upi"))
        s.commit()  # anomalies reference these rows
        s.add(Anomaly(id=1, anomaly_type="outlier", dedupe_key="outlier:4", transaction_id=4,
                      category="Software & Hosting", details="{}", note="Big VPN bill"))
        s.add(Anomaly(id=2, anomaly_type="category_spike", dedupe_key="category_spike:Dining:2026-08",
                      category="Dining", month="2026-08", details="{}", note="Dining spike",
                      status="dismissed"))
        s.commit()
    return engine


def test_months(client):
    assert client.get("/api/months").json() == ["2026-09", "2026-08"]


def test_summary_defaults_to_latest_month(client):
    s = client.get("/api/dashboard/summary").json()
    assert s == {
        "month": "2026-09",
        "spend_paise": 199653,  # transfers and credits excluded
        "income_paise": 600000,
        "txn_count": 5,
        "prev_month": "2026-08",
        "prev_spend_paise": 70000,
        "open_anomalies": 1,
        "uncategorized_count": 1,
        "top_categories": [
            {"category": "Software & Hosting", "spend_paise": 146413},
            {"category": "Uncategorized", "spend_paise": 30000},
            {"category": "Food & Groceries", "spend_paise": 17500},
        ],
    }


def test_summary_for_given_month(client):
    s = client.get("/api/dashboard/summary", params={"month": "2026-08"}).json()
    assert (s["spend_paise"], s["income_paise"], s["prev_spend_paise"]) == (70000, 500000, 0)


def test_summary_rejects_bad_month(client):
    assert client.get("/api/dashboard/summary", params={"month": "Sept"}).status_code == 422


def test_summary_empty_database(client, engine):
    with Session(engine) as s:
        for a in (s.get(Anomaly, 1), s.get(Anomaly, 2)):
            s.delete(a)
        s.commit()
        for id, *_ in ROWS:
            s.delete(s.get(Transaction, id))
        s.commit()
    s = client.get("/api/dashboard/summary").json()
    assert (s["month"], s["spend_paise"], s["top_categories"]) == (None, 0, [])


def test_category_breakdown(client):
    rows = client.get("/api/dashboard/categories", params={"month": "2026-09"}).json()
    assert rows == [
        {"category": "Software & Hosting", "spend_paise": 146413, "count": 1, "share": 0.733},
        {"category": "Uncategorized", "spend_paise": 30000, "count": 1, "share": 0.15},
        {"category": "Food & Groceries", "spend_paise": 17500, "count": 1, "share": 0.088},
        {"category": "Health", "spend_paise": 5740, "count": 1, "share": 0.029},
    ]


def test_daily_spend_fills_gaps(client):
    rows = client.get("/api/dashboard/daily", params={"month": "2026-09"}).json()
    assert rows == [
        {"date": "2026-09-01", "spend_paise": 146413, "cumulative_paise": 146413},
        {"date": "2026-09-02", "spend_paise": 23240, "cumulative_paise": 169653},
        {"date": "2026-09-03", "spend_paise": 0, "cumulative_paise": 169653},
        {"date": "2026-09-04", "spend_paise": 0, "cumulative_paise": 169653},
        {"date": "2026-09-05", "spend_paise": 30000, "cumulative_paise": 199653},
    ]


def test_monthly_trend_fills_gaps(client):
    rows = client.get("/api/dashboard/trend", params={"months": 3, "end": "2026-09"}).json()
    assert rows == [
        {"month": "2026-07", "spend_paise": 0, "income_paise": 0},
        {"month": "2026-08", "spend_paise": 70000, "income_paise": 500000},
        {"month": "2026-09", "spend_paise": 199653, "income_paise": 600000},
    ]


def test_categories_list(client):
    names = client.get("/api/categories").json()
    assert "Food & Groceries" in names and len(names) == 18


# --- transactions -----------------------------------------------------------------

def ids(response):
    return [t["id"] for t in response.json()["items"]]


def test_transactions_newest_first_with_total(client):
    r = client.get("/api/transactions")
    assert r.json()["total"] == 9
    assert ids(r) == [9, 8, 7, 6, 5, 4, 3, 2, 1]


def test_transaction_item_shape(client):
    [item] = client.get("/api/transactions", params={"q": "expressvpn"}).json()["items"]
    assert item == {
        "id": 4, "txn_date": "2026-09-01", "amount_paise": -146413,
        "merchant_normalized": "ExpressVPN", "narration": "NARRATION ExpressVPN",
        "category": "Software & Hosting", "category_source": "llm", "channel": "upi",
        "payee_type": None, "txn_type": "debit", "anomaly_types": ["outlier"],
    }


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"month": "2026-09", "category": "Food & Groceries"}, [6]),
        ({"category": "Uncategorized"}, [9]),
        ({"direction": "credit"}, [8, 3]),
        ({"direction": "debit", "month": "2026-08"}, [2, 1]),
        ({"q": "payroll"}, [8, 3]),
        ({"limit": 2, "offset": 2}, [7, 6]),
    ],
)
def test_transaction_filters(client, params, expected):
    assert ids(client.get("/api/transactions", params=params)) == expected


def test_transaction_total_reflects_filters_not_page(client):
    r = client.get("/api/transactions", params={"direction": "debit", "limit": 1})
    assert (r.json()["total"], len(r.json()["items"])) == (7, 1)


# --- anomalies ----------------------------------------------------------------------

def test_open_anomalies_with_transaction(client):
    [a] = client.get("/api/anomalies").json()
    assert a["id"] == 1 and a["note"] == "Big VPN bill" and a["status"] == "open"
    assert a["transaction"] == {"id": 4, "txn_date": "2026-09-01", "amount_paise": -146413,
                                "merchant_normalized": "ExpressVPN"}


def test_anomaly_status_filter(client):
    [a] = client.get("/api/anomalies", params={"status": "dismissed"}).json()
    assert (a["id"], a["transaction"], a["month"]) == (2, None, "2026-08")
    assert len(client.get("/api/anomalies", params={"status": "all"}).json()) == 2


def test_dismiss_and_reopen(client):
    assert client.patch("/api/anomalies/1", json={"status": "dismissed"}).json()["status"] == "dismissed"
    assert client.get("/api/anomalies").json() == []
    assert client.patch("/api/anomalies/1", json={"status": "open"}).status_code == 200


def test_anomaly_patch_validation(client):
    assert client.patch("/api/anomalies/1", json={"status": "gone"}).status_code == 422
    assert client.patch("/api/anomalies/99", json={"status": "open"}).status_code == 404


def test_frontend_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "<title>" in r.text
