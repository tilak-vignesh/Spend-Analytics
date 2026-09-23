"""Money is signed int paise everywhere. Never parse or compute through float."""

import re

_AMOUNT_RE = re.compile(r"^(-)?(\d+)(?:\.(\d{1,2}))?$")


def parse_paise(text: str) -> int:
    """Parse a statement amount like "1,464.13" or "686,919.9" into paise.

    Raises ValueError on blanks or anything that isn't a plain amount
    (callers decide what an empty Withdrawal/Deposit cell means).
    """
    cleaned = text.strip().replace(",", "")
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        raise ValueError(f"Not a valid amount: {text!r}")
    sign, rupees, fraction = match.groups()
    paise = int(rupees) * 100 + int((fraction or "0").ljust(2, "0"))
    return -paise if sign else paise


def format_inr(paise: int) -> str:
    """Format paise as rupees with Indian digit grouping: 68691991 -> "₹6,86,919.91"."""
    sign = "-" if paise < 0 else ""
    rupees, fraction = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}.{fraction:02d}"
