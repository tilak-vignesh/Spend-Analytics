"""Shared types for anomaly detectors. Detectors are pure functions over Txn lists."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Txn:
    """A spend (debit, non-transfer) transaction as the detectors see it."""

    id: int
    txn_date: date
    spend_paise: int  # positive
    merchant_key: str | None
    merchant_name: str | None
    category: str | None
    channel: str | None


@dataclass(frozen=True)
class Finding:
    anomaly_type: str
    dedupe_key: str
    note: str
    details: dict = field(default_factory=dict)
    transaction_id: int | None = None
    related_transaction_id: int | None = None
    category: str | None = None
    month: str | None = None


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def shift_month(month: str, delta: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def name_of(txn: Txn) -> str:
    return txn.merchant_name or txn.merchant_key or "unknown payee"
