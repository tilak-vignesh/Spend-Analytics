"""Statistical outliers: payments unusually large for their category.

Robust z-score (Iglewicz & Hoaglin): 0.6745 * (x - median) / MAD over the category's
payments in the previous ~6 months. Needs a minimum number of earlier payments, so
there are no stats-based flags until enough history exists (cold start).
"""

from collections import defaultdict
from datetime import timedelta
from statistics import mean, median

from app.anomalies.base import Finding, Txn, name_of
from app.money import format_inr


def _robust_z(x: int, samples: list[int]) -> float | None:
    mid = median(samples)
    mad = median(abs(s - mid) for s in samples)
    if mad:
        return 0.6745 * (x - mid) / mad
    mean_ad = mean(abs(s - mid) for s in samples)
    if mean_ad:  # over half the samples identical: fall back to mean absolute deviation
        return (x - mid) / (1.253314 * mean_ad)
    return None  # all samples identical


def find_outliers(txns: list[Txn], window_days: int = 183, min_samples: int = 10,
                  threshold: float = 3.5, min_spend_paise: int = 500_00,
                  identical_multiplier: float = 2.0) -> list[Finding]:
    by_category: dict[str, list[Txn]] = defaultdict(list)
    for txn in sorted(txns, key=lambda t: (t.txn_date, t.id)):
        if txn.category:
            by_category[txn.category].append(txn)

    findings = []
    for category, ordered in by_category.items():
        for i, txn in enumerate(ordered):
            if txn.spend_paise < min_spend_paise:
                continue
            since = txn.txn_date - timedelta(days=window_days)
            samples = [p.spend_paise for p in ordered[:i] if p.txn_date >= since]
            if len(samples) < min_samples:
                continue
            z = _robust_z(txn.spend_paise, samples)
            typical = median(samples)
            if z is None:
                flagged = txn.spend_paise > identical_multiplier * typical
            else:
                flagged = z > threshold
            if not flagged:
                continue
            findings.append(Finding(
                anomaly_type="outlier",
                dedupe_key=f"outlier:{txn.id}",
                transaction_id=txn.id,
                category=category,
                details={"spend_paise": txn.spend_paise, "median_paise": int(typical),
                         "samples": len(samples),
                         "robust_z": round(z, 1) if z is not None else None},
                note=(f"{format_inr(txn.spend_paise)} to {name_of(txn)} is unusually large "
                      f"for {category}: typically {format_inr(int(typical))} (median of "
                      f"{len(samples)} payments in the previous 6 months)"),
            ))
    return findings
