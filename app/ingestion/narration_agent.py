"""Batched LLM pass: parse + verify + categorize unseen payees from raw narrations.

The LLM is reached through a `generate_json(system=, prompt=, schema=)` callable
(see app/llm/gemini.py), so this module has no provider dependency. Everything the
model returns is treated as untrusted and validated here.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PAYEE_TYPES = ["merchant", "merchant_qr", "person"]

GenerateJSON = Callable[..., Any]

SYSTEM_PROMPT = """\
You identify and categorize payees from Indian bank statement (HDFC) narrations for a
personal finance tool.

Each input payee has:
- narration: raw text. "\\n" marks a PDF line wrap; the original text may or may not
  have had a space there ("UPI-RAVI\\nKUMAR" is "RAVI KUMAR", but "@OKA\\nXIS" is "@OKAXIS").
- channel: upi / pos / imps / neft / atm / other.
- vpa, ifsc: extracted by regex, may be null.
- direction: debit (money paid out) or credit (money received).

Common shapes: UPI narrations are usually UPI-<payee name>-<vpa>-<ifsc>-<ref>-<remark>;
POS narrations are card payments with the merchant descriptor at the end, sometimes
prefixed by a city.

Return exactly one item per input id with:
- payee_name: the payee as written, wraps resolved, or null.
- payee_vpa: the UPI ID copied exactly from the narration, or null. Never invent one.
- merchant_normalized: a short display name. Brand for businesses ("Swiggy Instamart",
  "Hetzner", "Apollo Pharmacy"); Title Case for people ("Ravi Kumar").
- payee_type: merchant (a business), merchant_qr (a small shop's QR code, often under
  the owner's personal name), or person (an individual; peer-to-peer).
- is_person_name: true if payee_name is an individual's name, even on a merchant QR.
- category: one of the allowed categories only. Dining = restaurants, cafes, food
  delivery; Food & Groceries = groceries, quick commerce, provision stores;
  Income = money received as salary, dividends ("ACH C-" from a company), interest
  or refunds; Investments = money paid into investment platforms or funds. Use Other
  if nothing fits.
- confidence: 0 to 1.
Use your knowledge of Indian merchants, payment apps and UPI handle conventions.
"""


@dataclass(frozen=True)
class PayeeInput:
    key: str
    narration: str
    channel: str | None
    vpa: str | None
    ifsc: str | None
    direction: str  # debit / credit


@dataclass(frozen=True)
class PayeeResult:
    payee_name: str | None
    payee_vpa: str | None
    merchant_normalized: str
    payee_type: str | None
    is_person_name: bool
    category: str
    confidence: float


@dataclass
class ClassifyOutcome:
    results: dict[str, PayeeResult] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)  # key -> reason


def response_schema(categories: list[str]) -> dict:
    nullable_str = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "payee_name": nullable_str,
                        "payee_vpa": nullable_str,
                        "merchant_normalized": {"type": "string"},
                        "payee_type": {"type": "string", "enum": PAYEE_TYPES},
                        "is_person_name": {"type": "boolean"},
                        "category": {"type": "string", "enum": categories},
                        "confidence": {"type": "number"},
                    },
                    "required": ["id", "payee_name", "payee_vpa", "merchant_normalized",
                                 "payee_type", "is_person_name", "category", "confidence"],
                },
            }
        },
        "required": ["items"],
    }


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _validate(raw: dict, payee: PayeeInput, categories: list[str]) -> PayeeResult | str:
    """A PayeeResult, or the reason the item was rejected."""
    category = raw.get("category")
    if category not in categories:
        return f"invalid category {category!r}"
    name = raw.get("merchant_normalized")
    if not isinstance(name, str) or not name.strip():
        return "missing merchant_normalized"

    vpa = raw.get("payee_vpa")
    if not (isinstance(vpa, str) and vpa.strip() and _squash(vpa) in _squash(payee.narration)):
        vpa = None  # not in the narration: the model made it up
    payee_name = raw.get("payee_name")
    try:
        confidence = min(max(float(raw.get("confidence", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.5

    return PayeeResult(
        payee_name=payee_name.strip() if isinstance(payee_name, str) and payee_name.strip() else None,
        payee_vpa=_squash(vpa) if vpa else None,
        merchant_normalized=name.strip(),
        payee_type=raw.get("payee_type") if raw.get("payee_type") in PAYEE_TYPES else None,
        is_person_name=raw.get("is_person_name") is True,
        category=category,
        confidence=confidence,
    )


def _classify_batch(batch: list[PayeeInput], categories: list[str],
                    generate_json: GenerateJSON, outcome: ClassifyOutcome) -> None:
    payload = [
        {"id": i, "narration": p.narration, "channel": p.channel, "vpa": p.vpa,
         "ifsc": p.ifsc, "direction": p.direction}
        for i, p in enumerate(batch)
    ]
    prompt = (
        f"Allowed categories: {json.dumps(categories)}\n\n"
        f"PAYEES:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        response = generate_json(system=SYSTEM_PROMPT, prompt=prompt,
                                 schema=response_schema(categories))
        items = response["items"]
        if not isinstance(items, list):
            raise TypeError("items is not a list")
    except Exception as exc:  # any provider/format failure fails just this batch
        for p in batch:
            outcome.failed[p.key] = f"LLM call failed: {exc}"
        return

    by_id = {it.get("id"): it for it in items if isinstance(it, dict)}
    for i, payee in enumerate(batch):
        raw = by_id.get(i)
        if raw is None:
            outcome.failed[payee.key] = "no result returned"
            continue
        result = _validate(raw, payee, categories)
        if isinstance(result, str):
            outcome.failed[payee.key] = result
        else:
            outcome.results[payee.key] = result


def classify_payees(inputs: list[PayeeInput], categories: list[str],
                    generate_json: GenerateJSON, batch_size: int = 50) -> ClassifyOutcome:
    outcome = ClassifyOutcome()
    for start in range(0, len(inputs), batch_size):
        _classify_batch(inputs[start:start + batch_size], categories, generate_json, outcome)
    return outcome
