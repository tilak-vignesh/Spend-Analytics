import os
from datetime import date
from pathlib import Path

import pytest

from app.ingestion.hdfc_pdf import StatementError, parse_statement
from tests.pdf_factory import balanced_rows, make_statement_pdf

OPENING = 100000_00
SPECS = [
    ("01/09/26", ["POS 416021XXXXXX0000 600000000001 01SEP2",
                  "6 14:26:06 +10000000000 EXAMPLEVPN.COM"], "0000600000000001", -146413),
    ("02/09/26", ["UPI-TEST", "PHARMACY-TESTPHARMACY@",
                  "YBL-YESB0YBLUPI-100000000002-PAYMENT FOR", "200017"],
     "0000100000000002", -5740),
    ("03/09/26", ["UPI-RAVI KUMAR-9000000000@YBL-SBIN00", "00001-100000000003-UPI"],
     "0000100000000003", -11200),
    ("04/09/26", ["IMPS-600000000004-ACME PAYROLL"], "0000600000000004", 600000),
    ("05/09/26", ["UPI-FRESH MART-Q000000005@YBL-YESB0Y", "BLUPI-100000000005-UPI"],
     "0000100000000005", -2000),
]


@pytest.fixture
def statement_pdf(tmp_path):
    path = tmp_path / "stmt.pdf"
    # 4 lines per page: the pharmacy row's narration spills onto page 2.
    make_statement_pdf(path, balanced_rows(OPENING, SPECS), lines_per_page=4,
                       opening_paise=OPENING)
    return path


def test_parses_rows_and_metadata(statement_pdf):
    stmt = parse_statement(statement_pdf, password="secret")

    assert stmt.account_number_last4 == "1234"
    assert (stmt.period_from, stmt.period_to) == (date(2026, 9, 1), date(2026, 9, 23))
    assert [r.amount_paise for r in stmt.rows] == [s[3] for s in SPECS]
    assert [r.ref_no for r in stmt.rows] == [s[2] for s in SPECS]
    assert stmt.rows[0].txn_date == date(2026, 9, 1)
    assert stmt.rows[0].value_date == date(2026, 9, 1)
    assert stmt.rows[-1].balance_after_paise == OPENING + sum(s[3] for s in SPECS)
    assert stmt.summary.dr_count == 4 and stmt.summary.cr_count == 1


def test_narration_continues_across_page_break(statement_pdf):
    stmt = parse_statement(statement_pdf, password="secret")
    assert stmt.rows[1].narration == (
        "UPI-TEST\nPHARMACY-TESTPHARMACY@\nYBL-YESB0YBLUPI-100000000002-PAYMENT FOR\n200017"
    )


def test_spaces_within_a_line_are_kept(statement_pdf):
    stmt = parse_statement(statement_pdf, password="secret")
    assert stmt.rows[2].narration.startswith("UPI-RAVI KUMAR-")


def test_footer_and_summary_are_not_narration(statement_pdf):
    stmt = parse_statement(statement_pdf, password="secret")
    text = "\n".join(r.narration for r in stmt.rows)
    assert "Contents of this statement" not in text
    assert "STATEMENT SUMMARY" not in text
    assert stmt.rows[-1].narration == "UPI-FRESH MART-Q000000005@YBL-YESB0Y\nBLUPI-100000000005-UPI"


def test_wrong_password(statement_pdf):
    with pytest.raises(StatementError, match="password"):
        parse_statement(statement_pdf, password="nope")


def test_missing_password_for_encrypted_pdf(statement_pdf):
    with pytest.raises(StatementError, match="password"):
        parse_statement(statement_pdf, password=None)


def test_unencrypted_pdf_needs_no_password(tmp_path):
    path = tmp_path / "plain.pdf"
    make_statement_pdf(path, balanced_rows(OPENING, SPECS), password=None,
                       opening_paise=OPENING)
    assert len(parse_statement(path, password=None).rows) == len(SPECS)


def test_summary_mismatch_is_rejected(tmp_path):
    path = tmp_path / "bad.pdf"
    make_statement_pdf(path, balanced_rows(OPENING, SPECS), opening_paise=OPENING,
                       summary_override={"dr": "5"})
    with pytest.raises(StatementError, match="debit count"):
        parse_statement(path, password="secret")


def test_not_a_statement(tmp_path):
    path = tmp_path / "junk.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(StatementError):
        parse_statement(path, password=None)


REAL = Path(__file__).parent.parent / "statements" / "acc.pdf"


@pytest.mark.skipif(
    not (REAL.exists() and os.environ.get("HDFC_PDF_PASSWORD")),
    reason="real statement / HDFC_PDF_PASSWORD not available",
)
def test_real_statement_reconciles():
    # parse_statement reconciles against the summary block, so success means every
    # balance carried and the counts/totals matched. Asserts nothing about content.
    stmt = parse_statement(REAL, password=os.environ["HDFC_PDF_PASSWORD"])
    assert len(stmt.rows) == stmt.summary.dr_count + stmt.summary.cr_count > 0
