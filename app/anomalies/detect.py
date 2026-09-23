"""Run every anomaly detector over the stored spends and sync the anomalies table.

Detection is a full recompute (a personal statement history is small). Findings are
matched to stored anomalies by dedupe_key: new ones are inserted, changed ones
updated, and open ones that no longer apply are removed. Dismissed anomalies are
never touched.
"""

import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.anomalies.base import Finding, Txn
from app.anomalies.rules import (
    find_category_spikes,
    find_duplicates,
    find_new_merchants,
    find_price_changes,
)
from app.anomalies.stats import find_outliers
from app.models import Anomaly, Transaction


@dataclass
class DetectResult:
    new: int = 0
    updated: int = 0
    removed: int = 0
    open_by_type: dict[str, int] = field(default_factory=dict)


def _spends(session: Session) -> list[Txn]:
    rows = session.exec(
        select(Transaction)
        .where(Transaction.amount_paise < 0)
        .where(Transaction.txn_type != "transfer")
    ).all()
    return [
        Txn(id=t.id, txn_date=t.txn_date, spend_paise=-t.amount_paise,
            merchant_key=t.merchant_key, merchant_name=t.merchant_normalized,
            category=t.category, channel=t.channel)
        for t in rows
    ]


def _find_all(spends: list[Txn], history_start) -> list[Finding]:
    return (
        find_duplicates(spends)
        + find_new_merchants(spends, history_start=history_start)
        + find_price_changes(spends)
        + find_category_spikes(spends, history_start=history_start)
        + find_outliers(spends)
    )


def detect_anomalies(engine: Engine) -> DetectResult:
    result = DetectResult()
    with Session(engine) as session:
        history_start = session.exec(select(func.min(Transaction.txn_date))).one()
        findings = _find_all(_spends(session), history_start) if history_start else []
        stored = {a.dedupe_key: a for a in session.exec(select(Anomaly)).all()}

        for f in findings:
            details = json.dumps(f.details, sort_keys=True)
            existing = stored.pop(f.dedupe_key, None)
            if existing is None:
                session.add(Anomaly(
                    anomaly_type=f.anomaly_type, dedupe_key=f.dedupe_key,
                    transaction_id=f.transaction_id,
                    related_transaction_id=f.related_transaction_id,
                    category=f.category, month=f.month, details=details, note=f.note,
                ))
                result.new += 1
            elif (existing.details, existing.note) != (details, f.note):
                existing.details, existing.note = details, f.note
                session.add(existing)
                result.updated += 1

        for leftover in stored.values():  # no longer detected
            if leftover.status == "open":
                session.delete(leftover)
                result.removed += 1
        session.commit()

        result.open_by_type = dict(Counter(
            session.exec(select(Anomaly.anomaly_type).where(Anomaly.status == "open")).all()
        ))
    return result
