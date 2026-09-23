"""Checks that every rupee amount and percentage in a chat answer came from a tool
result (or the user's own words), i.e. the model did no arithmetic itself.

A written figure is grounded if some source value, rounded to the precision the
figure is written at, equals it: "₹8,776" and "₹8.8k" are roundings of a returned
8775.96, but "₹1,358" computed as 23,358 − 22,000 matches nothing and is flagged.
Source numbers may be rupees or paise, so both readings are tried.
"""

import re

_SCALES = {"k": 1e3, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "cr": 1e7, "crore": 1e7}
_AMOUNT = re.compile(
    r"-?(?:₹|\bRs\.?|\bINR)\s?-?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<suffix>crore|cr|lakhs|lakh|k|L)\b)?",
    re.IGNORECASE,
)
_PERCENT = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s?(?:%|percentage points?\b|pp\b)", re.IGNORECASE)


def _written(num: str, scale: float = 1.0) -> tuple[int, float]:
    """(digits as an integer, value of one unit in the last written place)."""
    plain = num.replace(",", "")
    decimals = len(plain.split(".")[1]) if "." in plain else 0
    return int(plain.replace(".", "")), scale * 10 ** -decimals


def _amounts(text: str) -> list[tuple[str, int, float]]:
    out = []
    for m in _AMOUNT.finditer(text):
        scale = _SCALES[m["suffix"].lower()] if m["suffix"] else 1.0
        out.append((m.group(0).strip(), *_written(m["num"], scale)))
    return out


def _percents(text: str) -> list[tuple[str, int, float]]:
    return [(m.group(0), *_written(m["num"])) for m in _PERCENT.finditer(text)]


def _collect(value, numbers: list[float], strings: list[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        numbers.append(abs(float(value)))
    elif isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, numbers, strings)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, numbers, strings)


def _matches(digits: int, unit: float, candidates: set[float]) -> bool:
    return any(round(c / unit) == digits for c in candidates)


def unverified_figures(answer: str, tool_results: list, known_texts: list[str]) -> list[str]:
    """Figures in `answer` not traceable to `tool_results` or `known_texts`, in order."""
    numbers: list[float] = []
    strings: list[str] = list(known_texts)
    _collect(tool_results, numbers, strings)

    # Numbers may be rupees or paise; ₹ strings are rupees; percents may be shares.
    rupees = set(numbers) | {n / 100 for n in numbers}
    percents = set(numbers) | {n * 100 for n in numbers}
    for s in strings:
        rupees |= {digits * unit for _, digits, unit in _amounts(s)}
        percents |= {digits * unit for _, digits, unit in _percents(s)}

    flagged: list[str] = []
    checks = [(raw, d, u, rupees) for raw, d, u in _amounts(answer)]
    checks += [(raw, d, u, percents) for raw, d, u in _percents(answer)]
    for raw, digits, unit, candidates in checks:
        if not _matches(digits, unit, candidates) and raw not in flagged:
            flagged.append(raw)
    return flagged
