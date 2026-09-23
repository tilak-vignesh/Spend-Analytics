"""Cache key for a payee: its VPA when there is one, else a narration fingerprint."""

import re

_MONTH_DATE = re.compile(r"\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}")
_MASKED_CARD = re.compile(r"X{4,}")
_NOT_KEPT = re.compile(r"[^A-Z.&@+-]")
_DASH_RUN = re.compile(r"-{2,}")


def narration_fingerprint(narration: str) -> str:
    """Uppercase, drop all whitespace (wraps may or may not have been spaces), then
    drop anything that varies per transaction: dates like 01SEP26, masked card
    numbers, and every digit (refs, times, phone numbers, amounts)."""
    text = re.sub(r"\s+", "", narration.upper())
    text = _MONTH_DATE.sub("", text)
    text = _MASKED_CARD.sub("", text)
    text = _NOT_KEPT.sub("", text)
    return _DASH_RUN.sub("-", text).strip("-.")


def merchant_key(narration: str, vpa: str | None) -> str:
    if vpa:
        return f"vpa:{vpa}"
    return f"fp:{narration_fingerprint(narration)}"
