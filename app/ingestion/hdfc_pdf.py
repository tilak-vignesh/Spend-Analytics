"""HDFC savings account statement (password-protected PDF) parser.

The PDF has no real table structure: each cell is positioned text, narrations wrap
onto continuation lines (and across page breaks), and wrapped lines lose any space
that stood at the break. So narrations are kept with "\\n" at each wrap; whether a
wrap was a space is left for the LLM step to decide.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from app.money import parse_paise

# Right edge of each column, from the table's vertical rules. A word belongs to the
# column its horizontal centre falls in.
COLUMNS = [
    ("date", 67.5),
    ("narration", 255.0),
    ("ref", 357.0),
    ("value_date", 396.5),
    ("withdrawal", 474.5),
    ("deposit", 552.5),
    ("balance", float("inf")),
]
# Horizontal rules spanning the table are drawn as very wide, flat rects.
RULE_MIN_WIDTH = 500
# Words whose tops differ by less than this are on the same visual line.
LINE_TOLERANCE = 2.0

_ACCOUNT_RE = re.compile(r"Account\s*No\s*:\s*(\d+)")
_PERIOD_RE = re.compile(r"From\s*:\s*(\d{2}/\d{2}/\d{4})\s*To\s*:\s*(\d{2}/\d{2}/\d{4})")


class StatementError(ValueError):
    """The statement couldn't be parsed or doesn't reconcile."""


@dataclass(frozen=True)
class TableLine:
    """One visual line of the transaction table, text grouped by column."""

    date: str = ""
    narration: str = ""
    ref: str = ""
    value_date: str = ""
    withdrawal: str = ""
    deposit: str = ""
    balance: str = ""


@dataclass(frozen=True)
class StatementRow:
    txn_date: date
    narration: str
    ref_no: str | None
    value_date: date | None
    amount_paise: int  # negative = withdrawal
    balance_after_paise: int


@dataclass(frozen=True)
class Summary:
    opening_paise: int
    dr_count: int
    cr_count: int
    debits_paise: int
    credits_paise: int
    closing_paise: int


@dataclass(frozen=True)
class Statement:
    account_number_last4: str | None
    period_from: date
    period_to: date
    rows: list[StatementRow]
    summary: Summary | None


def _parse_short_date(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%y").date()


def _start_row(line: TableLine) -> dict:
    if bool(line.withdrawal) == bool(line.deposit):
        raise StatementError(
            f"Row dated {line.date} must have exactly one of withdrawal/deposit"
        )
    amount = -parse_paise(line.withdrawal) if line.withdrawal else parse_paise(line.deposit)
    return dict(
        txn_date=_parse_short_date(line.date),
        narration_lines=[line.narration] if line.narration else [],
        ref_no=line.ref or None,
        value_date=_parse_short_date(line.value_date) if line.value_date else None,
        amount_paise=amount,
        balance_after_paise=parse_paise(line.balance),
    )


def _finish_row(pending: dict) -> StatementRow:
    lines = pending.pop("narration_lines")
    return StatementRow(narration="\n".join(lines), **pending)


def build_rows(lines: list[TableLine]) -> list[StatementRow]:
    """Group table lines into transactions. A line with a date starts a new row;
    lines without one are narration continuations of the current row."""
    rows: list[StatementRow] = []
    pending: dict | None = None
    for line in lines:
        if line.date:
            if pending:
                rows.append(_finish_row(pending))
            pending = _start_row(line)
        elif pending is None:
            raise StatementError(
                f"Narration {line.narration!r} appears before the first transaction"
            )
        elif line.narration:
            pending["narration_lines"].append(line.narration)
    if pending:
        rows.append(_finish_row(pending))
    return rows


def reconcile(rows: list[StatementRow], summary: Summary | None) -> None:
    """Every closing balance must follow from the previous one, and totals must
    match the statement summary. Raises StatementError on the first mismatch."""
    balance = summary.opening_paise if summary else None
    for i, row in enumerate(rows, start=1):
        if balance is not None and balance + row.amount_paise != row.balance_after_paise:
            raise StatementError(
                f"Balance doesn't carry at row {i} ({row.txn_date}): "
                f"{balance} + {row.amount_paise} != {row.balance_after_paise}"
            )
        balance = row.balance_after_paise

    if summary is None:
        return
    debits = [-r.amount_paise for r in rows if r.amount_paise < 0]
    credits = [r.amount_paise for r in rows if r.amount_paise > 0]
    checks = [
        ("debit count", len(debits), summary.dr_count),
        ("credit count", len(credits), summary.cr_count),
        ("debit total", sum(debits), summary.debits_paise),
        ("credit total", sum(credits), summary.credits_paise),
        ("closing balance", balance, summary.closing_paise),
    ]
    for name, parsed, expected in checks:
        if parsed != expected:
            raise StatementError(f"Parsed {name} {parsed} != summary {expected}")


def parse_statement(path: str | Path, password: str | None) -> Statement:
    """Parse and reconcile an HDFC statement PDF. Raises StatementError."""
    try:
        with pdfplumber.open(path, password=password or "") as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            lines = [line for page in pdf.pages for line in _table_lines(page)]
            summary = _find_summary(pdf.pages)
    except PdfminerException as exc:
        if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
            raise StatementError("Wrong or missing statement password") from None
        raise StatementError(f"Not a readable PDF: {path}") from exc

    period = _PERIOD_RE.search(first_page_text)
    if not period:
        raise StatementError("No statement period found; is this an HDFC statement?")
    account = _ACCOUNT_RE.search(first_page_text)
    rows = build_rows(lines)
    if not rows:
        raise StatementError("No transactions found in statement")
    reconcile(rows, summary)
    return Statement(
        account_number_last4=account.group(1)[-4:] if account else None,
        period_from=datetime.strptime(period.group(1), "%d/%m/%Y").date(),
        period_to=datetime.strptime(period.group(2), "%d/%m/%Y").date(),
        rows=rows,
        summary=summary,
    )


def _table_lines(page) -> list[TableLine]:
    """Lines between the table's top rule and the next full-width rule below it."""
    rules = sorted(r["top"] for r in page.rects if r["width"] > RULE_MIN_WIDTH)
    if not rules:
        return []
    top = rules[0]
    bottom = next((t for t in rules[1:] if t > top + LINE_TOLERANCE), page.height)
    words = [w for w in page.extract_words(x_tolerance=1) if top < w["top"] < bottom]

    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) < LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])

    table_lines = []
    for line_words in lines:
        cells: dict[str, list[str]] = {}
        for word in sorted(line_words, key=lambda w: w["x0"]):
            centre = (word["x0"] + word["x1"]) / 2
            column = next(name for name, right in COLUMNS if centre < right)
            cells.setdefault(column, []).append(word["text"])
        line = TableLine(**{name: " ".join(parts) for name, parts in cells.items()})
        if line.date == "Date":  # column header row (first page only)
            continue
        table_lines.append(line)
    return table_lines


def _find_summary(pages) -> Summary | None:
    """The 'STATEMENT SUMMARY' block: a header line starting 'Opening Balance'
    followed by a line of six values."""
    for page in reversed(pages):
        text_lines = [l["text"] for l in page.extract_text_lines(x_tolerance=1)]
        for i, text in enumerate(text_lines[:-1]):
            if text.replace(" ", "").startswith("OpeningBalance"):
                values = text_lines[i + 1].split()
                if len(values) != 6:
                    raise StatementError(f"Unexpected summary values: {values}")
                opening, dr, cr, debits, credits, closing = values
                return Summary(
                    opening_paise=parse_paise(opening),
                    dr_count=int(dr),
                    cr_count=int(cr),
                    debits_paise=parse_paise(debits),
                    credits_paise=parse_paise(credits),
                    closing_paise=parse_paise(closing),
                )
    return None
