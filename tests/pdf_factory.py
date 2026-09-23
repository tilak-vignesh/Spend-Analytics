"""Builds synthetic HDFC-style statement PDFs for tests.

Mirrors the real layout that the parser relies on (measured from a real statement):
638x842 pages, 8pt Times-Roman table text, full-width rules above and below the
table, column x positions, the header row on page 1 only, narrations that wrap onto
continuation lines (and across pages), a 7pt footer and a summary block on the last
page. All names and numbers here are made up.
"""

from dataclasses import dataclass, field

from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = 638, 842
LINE_STEP = 17.2
TOP_RULE, BOTTOM_RULE = 227, 778
FIRST_LINE_TOP = 251


@dataclass
class FakeRow:
    date: str
    lines: list[str]
    ref: str
    withdrawal: str = ""
    deposit: str = ""
    balance: str = ""
    value_date: str = field(default="")

    def __post_init__(self):
        self.value_date = self.value_date or self.date


def _plain(paise: int) -> str:
    """Western-grouped amount as HDFC prints it: 68691991 -> '686,919.91'."""
    rupees, frac = divmod(paise, 100)
    return f"{rupees:,}.{frac:02d}"


def _y(top: float, size: float = 8) -> float:
    """reportlab baseline y for a pdfplumber-style 'top'."""
    return PAGE_H - top - size * 0.9


def _rule(c: canvas.Canvas, top: float) -> None:
    c.rect(28, PAGE_H - top, 603, 0.5, stroke=0, fill=1)


def _footer(c: canvas.Canvas) -> None:
    c.setFont("Times-Bold", 9)
    c.drawString(28, _y(785, 9), "HDFC BANK LIMITED")
    c.setFont("Times-Roman", 7)
    # Sits in the narration column's x range; the parser must not pick it up.
    c.drawString(74, _y(804, 7), "Contents of this statement will be considered correct")


def make_statement_pdf(
    path,
    rows: list[FakeRow],
    *,
    password: str | None = "secret",
    lines_per_page: int = 8,
    opening_paise: int = 100000_00,
    summary_override: dict | None = None,
    account_no: str = "50100000001234",
    period: tuple[str, str] = ("01/09/2026", "23/09/2026"),
) -> None:
    encrypt = StandardEncryption(password, canPrint=1) if password else None
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H), encrypt=encrypt)

    def page_header(first: bool) -> None:
        c.setFont("Times-Roman", 8)
        c.drawString(420, _y(160), f"AccountNo : {account_no}")
        c.drawString(34, _y(216), f"From : {period[0]}")
        c.drawString(154, _y(216), f"To : {period[1]}")
        c.setFont("Times-Roman", 12)
        c.drawString(340, _y(213, 12), "Statement of account")
        _rule(c, TOP_RULE)
        if first:
            c.setFont("Times-Roman", 8)
            for x, text in [(40, "Date"), (144, "Narration"), (284, "Chq./Ref.No."),
                            (362, "Value Dt"), (405, "Withdrawal Amt."),
                            (491, "Deposit Amt."), (564, "Closing Balance")]:
                c.drawString(x, _y(234), text)

    # Flatten rows into visual lines so narrations can spill across pages.
    visual = []
    for row in rows:
        for i, text in enumerate(row.lines):
            visual.append((row if i == 0 else None, text))

    page_header(first=True)
    top, used = FIRST_LINE_TOP, 0
    for row, text in visual:
        if used == lines_per_page:
            _rule(c, BOTTOM_RULE)
            _footer(c)
            c.showPage()
            page_header(first=False)
            top, used = FIRST_LINE_TOP - LINE_STEP, 0
        c.setFont("Times-Roman", 8)
        # Real statements wrap narrations inside the column (x 68-255).
        assert c.stringWidth(text, "Times-Roman", 8) < 255 - 72, f"narration too wide: {text}"
        c.drawString(72, _y(top), text)
        if row:
            c.drawString(34, _y(top), row.date)
            c.drawString(289, _y(top), row.ref)
            c.drawString(362, _y(top), row.value_date)
            if row.withdrawal:
                c.drawRightString(470, _y(top), row.withdrawal)
            if row.deposit:
                c.drawRightString(548, _y(top), row.deposit)
            c.drawRightString(627, _y(top), row.balance)
        top += LINE_STEP
        used += 1

    # Last page: table closes early, then the summary block.
    table_end = top + 10
    _rule(c, table_end)
    debits = [int(r.withdrawal.replace(",", "").replace(".", "")) for r in rows if r.withdrawal]
    credits = [int(r.deposit.replace(",", "").replace(".", "")) for r in rows if r.deposit]
    summary = dict(
        opening=_plain(opening_paise), dr=str(len(debits)), cr=str(len(credits)),
        debits=_plain(sum(debits)), credits=_plain(sum(credits)), closing=rows[-1].balance,
    )
    summary.update(summary_override or {})
    c.setFont("Times-Bold", 10)
    c.drawString(68, _y(table_end + 20, 10), "STATEMENT SUMMARY :-")
    c.setFont("Times-Bold", 8)
    headers = ["Opening Balance", "Dr Count", "Cr Count", "Debits", "Credits", "Closing Bal"]
    xs = [60, 170, 240, 330, 430, 560]
    for x, h in zip(xs, headers):
        c.drawString(x, _y(table_end + 32), h)
    c.setFont("Times-Roman", 8)
    values = [summary[k] for k in ["opening", "dr", "cr", "debits", "credits", "closing"]]
    for x, v in zip(xs, values):
        c.drawString(x, _y(table_end + 43), v)
    _rule(c, BOTTOM_RULE)
    _footer(c)
    c.save()


def balanced_rows(opening_paise: int, specs: list[tuple]) -> list[FakeRow]:
    """specs: (date, lines, ref, signed_paise). Fills withdrawal/deposit/balance."""
    rows, balance = [], opening_paise
    for date, lines, ref, amount in specs:
        balance += amount
        rows.append(FakeRow(
            date=date, lines=lines, ref=ref,
            withdrawal=_plain(-amount) if amount < 0 else "",
            deposit=_plain(amount) if amount > 0 else "",
            balance=_plain(balance),
        ))
    return rows

