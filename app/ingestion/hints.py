"""Best-effort, deterministic hints pulled from a raw narration.

Narrations have no reliable structure, so this only extracts tokens recognisable
anywhere in the string. Missing hints are fine; the LLM step verifies and fills the
rest. Wrapped lines ("\\n") are joined with no space first: VPAs and IFSCs never
contain spaces, and statement wraps often fall mid-token.
"""

import re
from dataclasses import dataclass

_CHANNEL_PREFIXES = [
    ("UPI", "upi"),
    ("POS", "pos"),
    ("NEFT", "neft"),
    ("IMPS", "imps"),
    ("NWD", "atm"),  # HDFC card cash withdrawal at another bank's ATM
    ("ATW", "atm"),  # ... at an HDFC ATM
]

# Payee names and UPI fields are separated by "-", so it's excluded from the handle.
_VPA_RE = re.compile(r"(?<![A-Za-z0-9._])[A-Za-z0-9._]+@[A-Za-z]+")
_IFSC_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Z0-9])")


@dataclass(frozen=True)
class Hints:
    channel: str
    vpa: str | None
    ifsc: str | None


def extract_hints(narration: str) -> Hints:
    joined = narration.replace("\n", "")
    channel = next(
        (name for prefix, name in _CHANNEL_PREFIXES if re.match(rf"{prefix}\b", joined)),
        "other",
    )
    if channel != "upi":
        return Hints(channel=channel, vpa=None, ifsc=None)

    vpa_match = _VPA_RE.search(joined)
    if not vpa_match:
        return Hints(channel=channel, vpa=None, ifsc=None)
    ifsc_match = _IFSC_RE.search(joined, vpa_match.end())
    return Hints(
        channel=channel,
        vpa=vpa_match.group().lower(),
        ifsc=ifsc_match.group() if ifsc_match else None,
    )
