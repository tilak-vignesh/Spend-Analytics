"""Cheap, rule-based anomaly checks. All amounts in paise; inputs are spends only."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from app.anomalies.base import Finding, Txn, month_of, name_of, shift_month
from app.money import format_inr


def _by_date(txns: list[Txn]) -> list[Txn]:
    return sorted(txns, key=lambda t: (t.txn_date, t.id))


def find_duplicates(txns: list[Txn], min_spend_paise: int = 500_00,
                    max_days_apart: int = 1) -> list[Finding]:
    """Same payee, same amount, same or next day. Small amounts are skipped: two
    identical chai payments are normal."""
    findings = []
    last_seen: dict[tuple[str, int], Txn] = {}
    for txn in _by_date(txns):
        if txn.merchant_key is None or txn.spend_paise < min_spend_paise:
            continue
        key = (txn.merchant_key, txn.spend_paise)
        prev = last_seen.get(key)
        if prev and (txn.txn_date - prev.txn_date).days <= max_days_apart:
            findings.append(Finding(
                anomaly_type="duplicate",
                dedupe_key=f"duplicate:{txn.id}",
                transaction_id=txn.id,
                related_transaction_id=prev.id,
                category=txn.category,
                details={"spend_paise": txn.spend_paise, "previous_date": prev.txn_date.isoformat()},
                note=(f"Possible duplicate: {format_inr(txn.spend_paise)} to {name_of(txn)} "
                      f"on {txn.txn_date}, same as {prev.txn_date}"),
            ))
        last_seen[key] = txn
    return findings


def find_new_merchants(txns: list[Txn], history_start: date,
                       min_spend_paise: int = 1000_00,
                       min_history_days: int = 60) -> list[Finding]:
    """First-ever payment to a payee above a threshold. Needs `min_history_days` of
    data before the payment, otherwise every payee is 'new'."""
    findings, seen = [], set()
    for txn in _by_date(txns):
        if txn.merchant_key is None or txn.merchant_key in seen:
            continue
        seen.add(txn.merchant_key)
        if (txn.spend_paise >= min_spend_paise
                and txn.txn_date - history_start >= timedelta(days=min_history_days)):
            findings.append(Finding(
                anomaly_type="new_merchant",
                dedupe_key=f"new_merchant:{txn.id}",
                transaction_id=txn.id,
                category=txn.category,
                details={"spend_paise": txn.spend_paise},
                note=f"First payment to {name_of(txn)}: {format_inr(txn.spend_paise)}",
            ))
    return findings


def find_price_changes(txns: list[Txn], tolerance: float = 0.02,
                       fx_tolerance: float = 0.08) -> list[Finding]:
    """A payee charged exactly once a month in at least 3 of the last 4 months
    (including this one) is recurring; flag when a charge moves beyond tolerance vs
    the previous one. Card (POS) charges are often billed in foreign currency, so
    they get a wider tolerance for exchange-rate drift."""
    by_key: dict[str, list[Txn]] = defaultdict(list)
    for txn in _by_date(txns):
        if txn.merchant_key:
            by_key[txn.merchant_key].append(txn)

    findings = []
    for charges in by_key.values():
        per_month: dict[str, int] = defaultdict(int)
        for c in charges:
            per_month[month_of(c.txn_date)] += 1
        for prev, txn in zip(charges, charges[1:]):
            window = [shift_month(month_of(txn.txn_date), -i) for i in range(4)]
            counts = [per_month.get(m, 0) for m in window]
            if counts[0] != 1 or max(counts) > 1 or sum(counts) < 3:
                continue
            change = (txn.spend_paise - prev.spend_paise) / prev.spend_paise
            limit = fx_tolerance if txn.channel == "pos" else tolerance
            if abs(change) <= limit:
                continue
            pct = round(change * 100, 1)
            findings.append(Finding(
                anomaly_type="price_change",
                dedupe_key=f"price_change:{txn.id}",
                transaction_id=txn.id,
                related_transaction_id=prev.id,
                category=txn.category,
                details={"spend_paise": txn.spend_paise, "previous_paise": prev.spend_paise,
                         "change_pct": pct},
                note=(f"{name_of(txn)} charged {format_inr(txn.spend_paise)}, "
                      f"{'up' if pct > 0 else 'down'} {abs(pct)}% from "
                      f"{format_inr(prev.spend_paise)} on {prev.txn_date}"),
            ))
    return findings


def find_category_spikes(txns: list[Txn], history_start: date, multiplier: float = 1.5,
                         min_total_paise: int = 2000_00, baseline_months: int = 6,
                         min_baseline_months: int = 3) -> list[Finding]:
    """A category's monthly total above `multiplier` x the median of its previous
    months (months with no spend count as zero) and above an absolute floor."""
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for txn in txns:
        if txn.category:
            totals[(txn.category, month_of(txn.txn_date))] += txn.spend_paise

    first_month = month_of(history_start)
    findings = []
    for (category, month), total in sorted(totals.items()):
        previous = [shift_month(month, -i) for i in range(1, baseline_months + 1)]
        baseline = [totals.get((category, m), 0) for m in previous if m >= first_month]
        if len(baseline) < min_baseline_months or total < min_total_paise:
            continue
        typical = int(median(baseline))
        if total <= multiplier * typical:
            continue
        findings.append(Finding(
            anomaly_type="category_spike",
            dedupe_key=f"category_spike:{category}:{month}",
            category=category,
            month=month,
            details={"total_paise": total, "baseline_median_paise": typical,
                     "baseline_months": len(baseline)},
            note=(
                f"{category} spend in {month} is {format_inr(total)}, "
                f"{total / typical:.1f}× the usual {format_inr(typical)} "
                f"(median of {len(baseline)} months)"
                if typical else
                f"{category} spend in {month} is {format_inr(total)}; usually none "
                f"(median of {len(baseline)} months is {format_inr(0)})"
            ),
        ))
    return findings
