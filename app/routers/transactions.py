from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from app.db import get_session
from app.models import Category, MerchantCategoryMap, Transaction
from app.routers.dashboard import Month, month_bounds

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

UNCATEGORIZED = "Uncategorized"


@router.get("")
def list_transactions(
    month: Month = None,
    category: str | None = None,  # "Uncategorized" matches rows with no category
    q: str | None = None,  # searches payee name and narration
    direction: Literal["debit", "credit"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    where, params = ["1 = 1"], {}
    if month:
        where.append("t.txn_date >= :start AND t.txn_date < :end")
        params["start"], params["end"] = month_bounds(month)
    if category == UNCATEGORIZED:
        where.append("t.category IS NULL")
    elif category:
        where.append("t.category = :category")
        params["category"] = category
    if q:
        where.append("(t.merchant_normalized LIKE :q OR t.narration LIKE :q)")
        params["q"] = f"%{q}%"
    if direction:
        where.append("t.amount_paise < 0" if direction == "debit" else "t.amount_paise > 0")
    clause = " AND ".join(where)

    conn = session.connection()
    total = conn.execute(text(f"SELECT COUNT(*) FROM transactions t WHERE {clause}"),
                         params).scalar()
    rows = conn.execute(text(f"""
        SELECT t.id, t.txn_date, t.amount_paise, t.merchant_normalized, t.narration,
               t.category, t.category_source, t.channel, t.payee_type, t.txn_type,
               (SELECT GROUP_CONCAT(a.anomaly_type) FROM anomalies a
                WHERE a.transaction_id = t.id AND a.status = 'open') AS anomaly_types
        FROM transactions t WHERE {clause}
        ORDER BY t.txn_date DESC, t.id DESC LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset}).mappings()
    items = []
    for r in rows:
        item = dict(r)
        item["anomaly_types"] = sorted(r["anomaly_types"].split(",")) if r["anomaly_types"] else []
        items.append(item)
    return {"total": total, "items": items}


class CategoryOverride(BaseModel):
    category: str
    # Also confirm this category for the payee: other non-manual transactions from
    # it are updated now, and future ones get it without the LLM.
    apply_to_payee: bool = False


class OverrideResult(BaseModel):
    id: int
    category: str
    category_source: str
    other_transactions_updated: int


@router.patch("/{txn_id}", response_model=OverrideResult)
def override_category(txn_id: int, body: CategoryOverride,
                      session: Session = Depends(get_session)) -> OverrideResult:
    txn = session.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, f"Transaction {txn_id} not found")
    if session.get(Category, body.category) is None:
        raise HTTPException(422, f"Unknown category {body.category!r}")
    if body.apply_to_payee and not txn.merchant_key:
        raise HTTPException(409, "Transaction has no payee yet; run categorize first")

    txn.category, txn.category_source = body.category, "manual"
    session.add(txn)

    updated = 0
    if body.apply_to_payee:
        entry = session.get(MerchantCategoryMap, txn.merchant_key) or MerchantCategoryMap(
            merchant_key=txn.merchant_key, payee_name=txn.payee_name,
            merchant_normalized=txn.merchant_normalized, payee_type=txn.payee_type,
            category=body.category,
        )
        entry.category, entry.source, entry.confidence = body.category, "manual", 1.0
        session.add(entry)
        siblings = session.exec(
            select(Transaction)
            .where(Transaction.merchant_key == txn.merchant_key)
            .where(Transaction.id != txn.id)
            .where(Transaction.category_source != "manual")
            .where(Transaction.txn_type != "transfer")
        ).all()
        for sibling in siblings:
            sibling.category, sibling.category_source = body.category, "rule"
            session.add(sibling)
        updated = len(siblings)

    session.commit()
    return OverrideResult(id=txn.id, category=txn.category,
                          category_source=txn.category_source,
                          other_transactions_updated=updated)
