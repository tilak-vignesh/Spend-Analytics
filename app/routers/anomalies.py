from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session

from app.db import get_session
from app.models import Anomaly

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


class StatusUpdate(BaseModel):
    status: Literal["open", "dismissed"]


@router.get("")
def list_anomalies(status: Literal["open", "dismissed", "all"] = "open",
                   session: Session = Depends(get_session)) -> list[dict]:
    rows = session.connection().execute(text("""
        SELECT a.id, a.anomaly_type, a.note, a.status, a.category, a.month, a.details,
               a.detected_at, t.id AS txn_id, t.txn_date, t.amount_paise, t.merchant_normalized
        FROM anomalies a LEFT JOIN transactions t ON t.id = a.transaction_id
        WHERE :status = 'all' OR a.status = :status
        ORDER BY COALESCE(t.txn_date, a.month || '-31') DESC, a.id DESC
    """), {"status": status}).mappings()
    return [
        {
            "id": r["id"], "anomaly_type": r["anomaly_type"], "note": r["note"],
            "status": r["status"], "category": r["category"], "month": r["month"],
            "detected_at": r["detected_at"],
            "transaction": None if r["txn_id"] is None else {
                "id": r["txn_id"], "txn_date": r["txn_date"], "amount_paise": r["amount_paise"],
                "merchant_normalized": r["merchant_normalized"]},
        }
        for r in rows
    ]


@router.patch("/{anomaly_id}")
def update_status(anomaly_id: int, body: StatusUpdate,
                  session: Session = Depends(get_session)) -> dict:
    anomaly = session.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(404, f"Anomaly {anomaly_id} not found")
    anomaly.status = body.status
    session.add(anomaly)
    session.commit()
    return {"id": anomaly.id, "status": anomaly.status}
