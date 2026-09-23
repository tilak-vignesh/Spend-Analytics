"""Deterministic payee-type signals from a UPI VPA / IFSC.

Strong signals are reliable enough to override the LLM (merchant QR schemes and
payment gateways have recognisable handles). Weak signals are personal-looking
handles, which small shops also use, so the LLM may override them.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    payee_type: str  # merchant / merchant_qr / person
    strong: bool


_MERCHANT_QR_IFSC = re.compile(r"MCHUPI$")  # e.g. YESB0MCHUPI (Paytm merchant)
_GATEWAY_IFSC = re.compile(r"0MERUPI$")  # e.g. HDFC0MERUPI (merchant collect)
_MERCHANT_QR_VPA = [
    re.compile(r"^q\d+@ybl$"),  # PhonePe merchant QR
    re.compile(r"^paytm\.s\w+@"),  # Paytm for Business
    re.compile(r"^paytmqr\w+@"),
    re.compile(r"^amzn\d+@apl$"),  # Amazon Pay merchant QR
    re.compile(r"^bharatpe\."),
    re.compile(r"^vyapar\."),
]
_GATEWAY_VPA = re.compile(r"\.(rzp|payu)@")  # Razorpay / PayU merchants
_PERSON_VPA = [
    re.compile(r"^\d{10}@"),  # mobile-number handle
    re.compile(r"@ok(axis|sbi|hdfcbank|icici)$"),  # Google Pay personal handles
]


def vpa_signal(vpa: str | None, ifsc: str | None) -> Signal | None:
    vpa = (vpa or "").lower()
    ifsc = (ifsc or "").upper()
    if _MERCHANT_QR_IFSC.search(ifsc) or any(p.search(vpa) for p in _MERCHANT_QR_VPA):
        return Signal("merchant_qr", strong=True)
    if _GATEWAY_IFSC.search(ifsc) or _GATEWAY_VPA.search(vpa):
        return Signal("merchant", strong=True)
    if any(p.search(vpa) for p in _PERSON_VPA):
        return Signal("person", strong=False)
    return None


def resolve_payee_type(llm_type: str | None, signal: Signal | None) -> str | None:
    if signal and signal.strong:
        return signal.payee_type
    return llm_type or (signal.payee_type if signal else None)
