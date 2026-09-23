from datetime import date

import pytest
from sqlmodel import Session

from app.chat.tools import ChatTools
from app.models import Account, Anomaly, Transaction

ROWS = [
    (1, date(2026, 8, 20), -50000, "Dining", "Swiggy", "debit"),
    (2, date(2026, 9, 1), -146413, "Software & Hosting", "ExpressVPN", "debit"),
    (3, date(2026, 9, 2), -17500, "Food & Groceries", "Ravi Kumar", "debit"),
    (4, date(2026, 9, 3), -2500000, "Transfers", "Own Card", "transfer"),
    (5, date(2026, 9, 4), 600000, "Income", "Acme Payroll", "credit"),
    (6, date(2026, 9, 5), -30000, "Dining", "District", "debit"),
]


@pytest.fixture
def tools(engine, db_path):
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        for id, d, amount, cat, payee, txn_type in ROWS:
            s.add(Transaction(id=id, account_id=1, txn_date=d, amount_paise=amount,
                              balance_after_paise=99999999, ref_no=f"000012345{id}",
                              dedupe_key=f"k{id}", narration=f"UPI-{payee}", category=cat,
                              merchant_normalized=payee, txn_type=txn_type))
        s.commit()
        s.add(Anomaly(anomaly_type="outlier", dedupe_key="outlier:2", transaction_id=2,
                      category="Software & Hosting", details="{}", note="Big VPN bill"))
        s.add(Anomaly(anomaly_type="category_spike", dedupe_key="category_spike:Dining:2026-08",
                      category="Dining", month="2026-08", details="{}", note="Dining spike",
                      status="dismissed"))
        s.commit()
    return ChatTools(db_path)


# --- run_sql ------------------------------------------------------------------------

def test_run_sql_on_chat_view(tools):
    r = tools.call("run_sql", {"query": "SELECT payee, amount_paise FROM chat_transactions "
                                        "WHERE id = 2"})
    assert r == {"columns": ["payee", "amount_paise"], "rows": [["ExpressVPN", -146413]],
                 "row_count": 1, "truncated": False}


def test_run_sql_allows_with_and_categories(tools):
    r = tools.call("run_sql", {"query": "-- spend\nWITH s AS (SELECT category, SUM(amount_paise) t "
                                        "FROM chat_transactions GROUP BY 1) "
                                        "SELECT c.name FROM categories c JOIN s ON s.category = c.name "
                                        "ORDER BY c.name LIMIT 1"})
    assert r["rows"] == [["Dining"]]


@pytest.mark.parametrize("query", [
    "SELECT balance_after_paise FROM transactions",  # underlying table
    "SELECT ref_no FROM transactions",
    "SELECT name FROM accounts",  # account names carry the last 4 digits
    "SELECT * FROM merchant_category_map",
    "SELECT sql FROM sqlite_master",
    "SELECT t.balance_after_paise FROM chat_transactions c JOIN transactions t USING (id)",
])
def test_run_sql_cannot_reach_restricted_data(tools, query):
    r = tools.call("run_sql", {"query": query})
    assert "error" in r and "rows" not in r
    assert "not allowed" in r["error"]


def test_restricted_columns_are_not_in_the_view(tools):
    r = tools.call("run_sql", {"query": "SELECT balance_after_paise FROM chat_transactions"})
    assert "error" in r


@pytest.mark.parametrize("query", [
    "DELETE FROM chat_transactions",
    "UPDATE categories SET name = 'x'",
    "PRAGMA table_info(transactions)",
    "ATTACH DATABASE ':memory:' AS other",
    "SELECT 1; DELETE FROM categories",
    "",
])
def test_run_sql_rejects_non_select(tools, query):
    assert "error" in tools.call("run_sql", {"query": query})


def test_run_sql_allows_aggregates_over_ctes(tools):
    r = tools.call("run_sql", {"query": "WITH d AS (SELECT * FROM chat_transactions "
                                        "WHERE category = 'Dining') SELECT COUNT(*) FROM d"})
    assert r["rows"] == [[2]]


def test_cte_cannot_launder_restricted_tables(tools):
    r = tools.call("run_sql", {"query": "WITH x AS (SELECT balance_after_paise FROM transactions) "
                                        "SELECT COUNT(*) FROM x"})
    assert "not allowed" in r["error"]


def test_run_sql_caps_rows(tools):
    r = tools.call("run_sql", {"query": "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL "
                                        "SELECT x + 1 FROM n WHERE x < 500) SELECT x FROM n"})
    assert (r["row_count"], r["truncated"], len(r["rows"])) == (200, True, 200)


def test_run_sql_times_out(db_path, tools):
    slow = ChatTools(db_path, timeout_ms=200)
    r = slow.call("run_sql", {"query": "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL "
                                       "SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"})
    assert "too long" in r["error"]


# --- structured tools -----------------------------------------------------------------

def test_category_breakdown_excludes_transfers_and_credits(tools):
    r = tools.call("get_category_breakdown", {"start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert r["total_spend_paise"] == 146413 + 17500 + 30000
    assert r["total_spend"] == "₹1,939.13"
    assert r["categories"][0] == {"category": "Software & Hosting", "spend_paise": 146413,
                                  "spend": "₹1,464.13", "count": 1}
    assert [c["category"] for c in r["categories"]] == ["Software & Hosting", "Dining",
                                                        "Food & Groceries"]


def test_category_breakdown_end_date_is_inclusive(tools):
    r = tools.call("get_category_breakdown", {"start_date": "2026-09-05", "end_date": "2026-09-05"})
    assert r["total_spend_paise"] == 30000


def test_anomalies_default_open(tools):
    r = tools.call("get_anomalies", {})
    assert r == {"anomalies": [{"id": 1, "type": "outlier", "status": "open",
                                "date": "2026-09-01", "category": "Software & Hosting",
                                "note": "Big VPN bill"}]}


def test_anomalies_filters(tools):
    r = tools.call("get_anomalies", {"status": "all", "start_date": "2026-08-01",
                                     "end_date": "2026-08-31"})
    assert [a["type"] for a in r["anomalies"]] == ["category_spike"]


@pytest.mark.parametrize("name, args", [
    ("get_category_breakdown", {"start_date": "Sept 1", "end_date": "2026-09-30"}),
    ("get_category_breakdown", {"start_date": "2026-09-01"}),
    ("get_anomalies", {"status": "whatever"}),
    ("no_such_tool", {}),
])
def test_bad_calls_return_errors_not_exceptions(tools, name, args):
    assert "error" in tools.call(name, args)


def test_specs_describe_every_tool(tools):
    names = [s["name"] for s in tools.specs]
    assert names == ["run_sql", "get_category_breakdown", "get_anomalies"]
    for spec in tools.specs:
        assert spec["description"] and spec["parameters"]["type"] == "object"
