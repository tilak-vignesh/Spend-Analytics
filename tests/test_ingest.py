from dataclasses import replace
from datetime import date

import pytest
from sqlmodel import Session, select

from app.db import make_engine
from app.ingestion.hdfc_pdf import Statement, StatementRow
from app.ingestion.ingest import dedupe_key, ingest_statement
from app.models import Account, Transaction


def row(d, amount, balance, ref, narration="UPI-SHOP-q000000001@ybl-YESB0YBLUPI-1-UPI"):
    return StatementRow(txn_date=date(2026, 9, d), narration=narration, ref_no=ref,
                        value_date=date(2026, 9, d), amount_paise=amount,
                        balance_after_paise=balance)


ROWS = [
    row(1, -146413, 9853587, "0000600000000001",
        "POS 416021XXXXXX0000 600000000001 01SEP2\n6 14:26:06 EXAMPLEVPN.COM"),
    row(2, -5740, 9847847, "0000100000000002",
        "UPI-TEST\nSTORE-TESTSTOREQR12@OKA\nXIS-UBIN09167\n81-100000000002-UPI"),
    row(4, 600000, 10447847, "0000600000000004", "IMPS-600000000004-ACME PAYROLL"),
]


def statement(rows=ROWS, last4="1234"):
    return Statement(account_number_last4=last4, period_from=date(2026, 9, 1),
                     period_to=date(2026, 9, 23), rows=rows, summary=None)


@pytest.fixture
def engine(db_path):
    return make_engine(db_path)


def stored(engine):
    with Session(engine) as s:
        return s.exec(select(Transaction).order_by(Transaction.id)).all()


def test_inserts_rows_and_creates_account(engine):
    result = ingest_statement(engine, statement(), source_file="/some/where/sep.pdf")
    assert (result.inserted, result.skipped) == (3, 0)

    with Session(engine) as s:
        [account] = s.exec(select(Account)).all()
    assert account.name == "HDFC Savings 1234"
    assert account.account_type == "savings"
    assert result.account_id == account.id


def test_stored_fields(engine):
    ingest_statement(engine, statement(), source_file="/some/where/sep.pdf")
    pos, upi, imps = stored(engine)

    assert pos.amount_paise == -146413 and pos.balance_after_paise == 9853587
    assert pos.txn_date == date(2026, 9, 1) and pos.value_date == date(2026, 9, 1)
    assert pos.narration == ROWS[0].narration  # wraps preserved as \n
    assert pos.ref_no == "0000600000000001"
    assert (pos.channel, pos.txn_type) == ("pos", "debit")
    assert pos.source_file == "sep.pdf"  # basename only, no local paths

    assert (upi.channel, upi.payee_vpa, upi.payee_ifsc) == ("upi", "teststoreqr12@okaxis", "UBIN0916781")
    assert (imps.channel, imps.txn_type) == ("imps", "credit")
    # Categorization is a later phase.
    assert all(t.category is None and t.merchant_key is None for t in (pos, upi, imps))


def test_reingesting_same_statement_is_a_no_op(engine):
    ingest_statement(engine, statement(), source_file="sep.pdf")
    result = ingest_statement(engine, statement(), source_file="sep.pdf")
    assert (result.inserted, result.skipped) == (0, 3)
    assert len(stored(engine)) == 3


def test_overlapping_statement_only_adds_new_rows(engine):
    ingest_statement(engine, statement(ROWS[:2]), source_file="a.pdf")
    result = ingest_statement(engine, statement(ROWS[1:]), source_file="b.pdf")
    assert (result.inserted, result.skipped) == (1, 1)
    assert [t.ref_no for t in stored(engine)] == [r.ref_no for r in ROWS]


def test_same_account_reused_across_statements(engine):
    ingest_statement(engine, statement(ROWS[:1]), source_file="a.pdf")
    ingest_statement(engine, statement(ROWS[1:]), source_file="b.pdf")
    with Session(engine) as s:
        assert len(s.exec(select(Account)).all()) == 1


def test_different_accounts_are_kept_apart(engine):
    a = ingest_statement(engine, statement(last4="1234"), source_file="a.pdf")
    b = ingest_statement(engine, statement(last4="9999"), source_file="b.pdf")
    assert a.account_id != b.account_id
    assert b.inserted == 3  # same refs, different account -> not duplicates


def test_reused_ref_for_a_different_transaction_is_not_dropped(engine):
    reversal = replace(ROWS[0], txn_date=date(2026, 9, 3), amount_paise=146413,
                       balance_after_paise=9994260)
    result = ingest_statement(engine, statement([ROWS[0], reversal]), source_file="a.pdf")
    assert result.inserted == 2


def test_all_zero_refs_fall_back_to_content_hash(engine):
    # Two identical ₹20 payments on the same day, no usable ref: closing balance differs.
    a = row(5, -2000, 1000, "000000000000000", "CHAI")
    b = row(5, -2000, -1000, "000000000000000", "CHAI")
    first = ingest_statement(engine, statement([a, b]), source_file="a.pdf")
    again = ingest_statement(engine, statement([a, b]), source_file="a.pdf")
    assert (first.inserted, again.inserted, again.skipped) == (2, 0, 2)


def test_dedupe_key_format():
    assert dedupe_key(7, ROWS[0]) == "7:0000600000000001:2026-09-01:-146413"
    no_ref = replace(ROWS[0], ref_no=None)
    zero_ref = replace(ROWS[0], ref_no="0000000000000000")
    assert dedupe_key(7, no_ref).startswith("7:sha256:")
    assert dedupe_key(7, no_ref) == dedupe_key(7, zero_ref)
    assert dedupe_key(7, no_ref) != dedupe_key(8, no_ref)
