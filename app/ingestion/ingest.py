"""Write a parsed statement into the transactions table, skipping rows already stored."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.ingestion.hdfc_pdf import Statement, StatementRow
from app.ingestion.hints import extract_hints
from app.models import Account, Transaction


@dataclass(frozen=True)
class IngestResult:
    account_id: int
    account_name: str
    inserted: int
    skipped: int


def dedupe_key(account_id: int, row: StatementRow) -> str:
    """Identical for the same row seen in overlapping statements; includes date and
    amount so a bank-reused ref for a different transaction isn't dropped. Rows
    without a usable ref hash their content, closing balance included, so two
    identical payments on the same day stay distinct."""
    if row.ref_no and row.ref_no.strip("0"):
        return f"{account_id}:{row.ref_no}:{row.txn_date.isoformat()}:{row.amount_paise}"
    content = "|".join(
        str(part)
        for part in (row.txn_date.isoformat(), row.amount_paise, row.narration,
                     row.balance_after_paise)
    )
    return f"{account_id}:sha256:{hashlib.sha256(content.encode()).hexdigest()}"


def account_name(statement: Statement) -> str:
    return " ".join(filter(None, ["HDFC Savings", statement.account_number_last4]))


def _get_or_create_account(session: Session, name: str) -> int:
    account = session.exec(select(Account).where(Account.name == name)).first()
    if account is None:
        account = Account(name=name, account_type="savings")
        session.add(account)
        session.flush()
    return account.id


def ingest_statement(engine: Engine, statement: Statement, source_file: str) -> IngestResult:
    """Insert all rows in one transaction; duplicates (by dedupe_key) are skipped."""
    with Session(engine) as session:
        name = account_name(statement)
        account_id = _get_or_create_account(session, name)
        inserted = 0
        for row in statement.rows:
            hints = extract_hints(row.narration)
            stmt = insert(Transaction).values(
                account_id=account_id,
                txn_date=row.txn_date,
                value_date=row.value_date,
                amount_paise=row.amount_paise,
                balance_after_paise=row.balance_after_paise,
                ref_no=row.ref_no,
                dedupe_key=dedupe_key(account_id, row),
                narration=row.narration,
                channel=hints.channel,
                payee_vpa=hints.vpa,
                payee_ifsc=hints.ifsc,
                txn_type="debit" if row.amount_paise < 0 else "credit",
                source_file=Path(source_file).name,
            ).on_conflict_do_nothing(index_elements=["dedupe_key"])
            inserted += session.exec(stmt).rowcount
        session.commit()
    return IngestResult(
        account_id=account_id,
        account_name=name,
        inserted=inserted,
        skipped=len(statement.rows) - inserted,
    )
