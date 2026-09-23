"""Read-only aggregates for the dashboard. Plain SQL; money in int paise.
Spend = debits, income = credits; own-account transfers are excluded from both."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlmodel import Session

from app.anomalies.base import shift_month
from app.db import get_session

router = APIRouter(prefix="/api", tags=["dashboard"])

Month = Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
NOT_TRANSFER = "txn_type IS NOT 'transfer'"


def _rows(session: Session, sql: str, **params) -> list[dict]:
    return [dict(r) for r in session.connection().execute(text(sql), params).mappings()]


def _scalar(session: Session, sql: str, **params):
    return session.connection().execute(text(sql), params).scalar()


def month_bounds(month: str) -> tuple[str, str]:
    """[first day, first day of next month) as ISO strings, for index-friendly ranges."""
    return f"{month}-01", f"{shift_month(month, 1)}-01"


def latest_month(session: Session) -> str | None:
    return _scalar(session, "SELECT substr(MAX(txn_date), 1, 7) FROM transactions")


def _spend(session: Session, month: str) -> int:
    start, end = month_bounds(month)
    return _scalar(session, f"""
        SELECT COALESCE(SUM(-amount_paise), 0) FROM transactions
        WHERE amount_paise < 0 AND {NOT_TRANSFER} AND txn_date >= :start AND txn_date < :end
    """, start=start, end=end)


def _category_breakdown(session: Session, month: str) -> list[dict]:
    start, end = month_bounds(month)
    rows = _rows(session, f"""
        SELECT COALESCE(category, 'Uncategorized') AS category,
               SUM(-amount_paise) AS spend_paise, COUNT(*) AS count
        FROM transactions
        WHERE amount_paise < 0 AND {NOT_TRANSFER} AND txn_date >= :start AND txn_date < :end
        GROUP BY 1 ORDER BY spend_paise DESC, category
    """, start=start, end=end)
    total = sum(r["spend_paise"] for r in rows)
    for r in rows:
        r["share"] = round(r["spend_paise"] / total, 3) if total else 0
    return rows


@router.get("/months")
def months(session: Session = Depends(get_session)) -> list[str]:
    return [r["month"] for r in _rows(session, """
        SELECT DISTINCT substr(txn_date, 1, 7) AS month FROM transactions ORDER BY month DESC
    """)]


@router.get("/categories")
def categories(session: Session = Depends(get_session)) -> list[str]:
    return [r["name"] for r in _rows(session, "SELECT name FROM categories ORDER BY name")]


@router.get("/dashboard/summary")
def summary(month: Month = None, session: Session = Depends(get_session)) -> dict:
    open_anomalies = _scalar(session, "SELECT COUNT(*) FROM anomalies WHERE status = 'open'")
    month = month or latest_month(session)
    if month is None:
        return dict(month=None, spend_paise=0, income_paise=0, txn_count=0, prev_month=None,
                    prev_spend_paise=0, open_anomalies=open_anomalies,
                    uncategorized_count=0, top_categories=[])
    start, end = month_bounds(month)
    totals = _rows(session, f"""
        SELECT COALESCE(SUM(CASE WHEN amount_paise > 0 THEN amount_paise END), 0) AS income,
               COUNT(*) AS txn_count,
               COALESCE(SUM(category IS NULL), 0) AS uncategorized
        FROM transactions
        WHERE {NOT_TRANSFER} AND txn_date >= :start AND txn_date < :end
    """, start=start, end=end)[0]
    prev = shift_month(month, -1)
    return dict(
        month=month,
        spend_paise=_spend(session, month),
        income_paise=totals["income"],
        txn_count=totals["txn_count"],
        prev_month=prev,
        prev_spend_paise=_spend(session, prev),
        open_anomalies=open_anomalies,
        uncategorized_count=totals["uncategorized"],
        top_categories=[{"category": r["category"], "spend_paise": r["spend_paise"]}
                        for r in _category_breakdown(session, month)[:3]],
    )


@router.get("/dashboard/categories")
def category_breakdown(month: Month = None, session: Session = Depends(get_session)) -> list[dict]:
    month = month or latest_month(session)
    return _category_breakdown(session, month) if month else []


@router.get("/dashboard/daily")
def daily(month: Month = None, session: Session = Depends(get_session)) -> list[dict]:
    """Spend per day from the 1st to the last day with data, gaps filled with 0."""
    month = month or latest_month(session)
    if month is None:
        return []
    start, end = month_bounds(month)
    last = _scalar(session, "SELECT MAX(txn_date) FROM transactions "
                            "WHERE txn_date >= :start AND txn_date < :end", start=start, end=end)
    if last is None:
        return []
    by_day = {r["d"]: r["spend"] for r in _rows(session, f"""
        SELECT txn_date AS d, SUM(-amount_paise) AS spend FROM transactions
        WHERE amount_paise < 0 AND {NOT_TRANSFER} AND txn_date >= :start AND txn_date < :end
        GROUP BY txn_date
    """, start=start, end=end)}
    out, running, day = [], 0, date.fromisoformat(start)
    while day <= date.fromisoformat(last):
        spend = by_day.get(day.isoformat(), 0)
        running += spend
        out.append({"date": day.isoformat(), "spend_paise": spend, "cumulative_paise": running})
        day += timedelta(days=1)
    return out


@router.get("/dashboard/trend")
def trend(months: Annotated[int, Query(ge=1, le=36)] = 6, end: Month = None,
          session: Session = Depends(get_session)) -> list[dict]:
    """Monthly spend and income for the `months` months ending at `end`."""
    end = end or latest_month(session)
    if end is None:
        return []
    window = [shift_month(end, -i) for i in reversed(range(months))]
    start, stop = month_bounds(window[0])[0], month_bounds(end)[1]
    by_month = {r["month"]: r for r in _rows(session, f"""
        SELECT substr(txn_date, 1, 7) AS month,
               COALESCE(SUM(CASE WHEN amount_paise < 0 THEN -amount_paise END), 0) AS spend_paise,
               COALESCE(SUM(CASE WHEN amount_paise > 0 THEN amount_paise END), 0) AS income_paise
        FROM transactions
        WHERE {NOT_TRANSFER} AND txn_date >= :start AND txn_date < :stop
        GROUP BY 1
    """, start=start, stop=stop)}
    return [{"month": m,
             "spend_paise": by_month.get(m, {}).get("spend_paise", 0),
             "income_paise": by_month.get(m, {}).get("income_paise", 0)} for m in window]
