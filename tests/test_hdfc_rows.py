from datetime import date

import pytest

from app.ingestion.hdfc_pdf import (
    StatementError,
    StatementRow,
    Summary,
    TableLine,
    build_rows,
    reconcile,
)


def line(**cells) -> TableLine:
    return TableLine(**cells)


POS_LINES = [
    line(
        date="01/09/26",
        narration="POS 416021XXXXXX1596 624408798431 01SEP2",
        ref="0000624408798431",
        value_date="01/09/26",
        withdrawal="1,464.13",
        balance="686,919.91",
    ),
    line(narration="6 14:26:06 +13106018492 EXPRESSVPN.COM"),
]


def test_single_row_with_continuation():
    [row] = build_rows(POS_LINES)
    assert row == StatementRow(
        txn_date=date(2026, 9, 1),
        narration="POS 416021XXXXXX1596 624408798431 01SEP2\n6 14:26:06 +13106018492 EXPRESSVPN.COM",
        ref_no="0000624408798431",
        value_date=date(2026, 9, 1),
        amount_paise=-146413,
        balance_after_paise=68691991,
    )


def test_deposit_is_positive():
    [row] = build_rows(
        [line(date="05/09/26", narration="IMPS-1234", ref="0000000000001234",
              value_date="05/09/26", deposit="6,000.00", balance="1,00,000.00")]
    )
    assert row.amount_paise == 600000


def test_multiple_rows_keep_their_own_continuations():
    rows = build_rows(
        POS_LINES
        + [
            line(date="02/09/26", narration="UPI-APOLLO", ref="0000128895684855",
                 value_date="02/09/26", withdrawal="57.40", balance="686,862.51"),
            line(narration="PHARMACY-APOLLOPHARMACYKARPL@"),
            line(narration="YBL-YESB0YBLUPI-128895684855-PAYMENT FOR"),
            line(narration="208017"),
        ]
    )
    assert len(rows) == 2
    assert rows[1].narration == (
        "UPI-APOLLO\nPHARMACY-APOLLOPHARMACYKARPL@\nYBL-YESB0YBLUPI-128895684855-PAYMENT FOR\n208017"
    )
    assert rows[0].narration.count("\n") == 1


def test_continuation_before_first_row_is_an_error():
    with pytest.raises(StatementError, match="before the first transaction"):
        build_rows([line(narration="ORPHAN")])


@pytest.mark.parametrize(
    "amounts",
    [dict(), dict(withdrawal="1.00", deposit="1.00")],
    ids=["neither", "both"],
)
def test_row_needs_exactly_one_amount(amounts):
    with pytest.raises(StatementError, match="exactly one of withdrawal/deposit"):
        build_rows([line(date="01/09/26", narration="X", ref="1", value_date="01/09/26",
                         balance="10.00", **amounts)])


def test_blank_ref_becomes_none():
    [row] = build_rows([line(date="01/09/26", narration="INTEREST", withdrawal="1.00",
                             balance="10.00")])
    assert row.ref_no is None
    assert row.value_date is None


def _row(amount, balance, d=1):
    return StatementRow(txn_date=date(2026, 9, d), narration="X", ref_no=None,
                        value_date=None, amount_paise=amount, balance_after_paise=balance)


ROWS = [_row(-100, 900), _row(-50, 850, 2), _row(300, 1150, 3)]
SUMMARY = Summary(opening_paise=1000, dr_count=2, cr_count=1, debits_paise=150,
                  credits_paise=300, closing_paise=1150)


def test_reconcile_passes_on_consistent_statement():
    reconcile(ROWS, SUMMARY)


def test_reconcile_without_summary_checks_continuity_only():
    reconcile(ROWS, None)
    with pytest.raises(StatementError, match="row 2"):
        reconcile([ROWS[0], _row(-50, 800, 2)], None)


@pytest.mark.parametrize(
    "summary_change, message",
    [
        (dict(opening_paise=999), "row 1"),
        (dict(dr_count=3), "debit count"),
        (dict(cr_count=0), "credit count"),
        (dict(debits_paise=151), "debit total"),
        (dict(credits_paise=1), "credit total"),
        (dict(closing_paise=1), "closing balance"),
    ],
)
def test_reconcile_detects_mismatches(summary_change, message):
    bad = Summary(**{**SUMMARY.__dict__, **summary_change})
    with pytest.raises(StatementError, match=message):
        reconcile(ROWS, bad)
