"""Pair money moved between the user's own accounts (e.g. savings -> card bill) so
it isn't counted as spend twice."""

from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import Transaction

TRANSFER_CATEGORY = "Transfers"


def mark_transfers(engine: Engine, window_days: int = 3) -> int:
    """Pair debits with same-amount credits in a different account within
    `window_days`, closest dates first; each transaction pairs at most once.
    Returns the number of pairs marked."""
    with Session(engine) as session:
        candidates = session.exec(
            select(Transaction)
            .where(Transaction.txn_type.in_(["debit", "credit"]))
            .where(or_(Transaction.category_source.is_(None),
                       Transaction.category_source != "manual"))
            .order_by(Transaction.txn_date, Transaction.id)
        ).all()
        debits = [t for t in candidates if t.amount_paise < 0]
        credits = [t for t in candidates if t.amount_paise > 0]
        # Rank every possible pair by date distance so the closest pairs win,
        # regardless of which debit comes first.
        pairs = sorted(
            (abs((c.txn_date - d.txn_date).days), d.txn_date, d.id, c.id, d, c)
            for d in debits
            for c in credits
            if c.account_id != d.account_id
            and c.amount_paise == -d.amount_paise
            and abs((c.txn_date - d.txn_date).days) <= window_days
        )
        used: set[int] = set()
        paired = 0
        for *_, debit, credit in pairs:
            if debit.id in used or credit.id in used:
                continue
            used.update((debit.id, credit.id))
            for t in (debit, credit):
                t.txn_type, t.category, t.category_source = "transfer", TRANSFER_CATEGORY, "rule"
                session.add(t)
            paired += 1
        session.commit()
    return paired
