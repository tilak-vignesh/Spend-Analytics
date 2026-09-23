from datetime import date

from app.anomalies.base import Txn
from app.anomalies.rules import (
    find_category_spikes,
    find_duplicates,
    find_new_merchants,
    find_price_changes,
)

_ids = iter(range(1, 100_000))


def t(when, rupees, key="vpa:shop@ybl", cat="Dining", channel="upi", name="Shop", id=None):
    y, m, d = when
    return Txn(id=id or next(_ids), txn_date=date(y, m, d), spend_paise=round(rupees * 100),
               merchant_key=key, merchant_name=name, category=cat, channel=channel)


# --- duplicates -----------------------------------------------------------------

def test_duplicate_same_payee_amount_and_day():
    a, b = t((2026, 9, 1), 1464.13, name="ExampleVPN"), t((2026, 9, 1), 1464.13, name="ExampleVPN")
    [f] = find_duplicates([a, b])
    assert (f.anomaly_type, f.transaction_id, f.related_transaction_id) == ("duplicate", b.id, a.id)
    assert f.dedupe_key == f"duplicate:{b.id}"
    assert f.note == "Possible duplicate: ₹1,464.13 to ExampleVPN on 2026-09-01, same as 2026-09-01"


def test_duplicate_next_day_counts_but_not_later():
    assert len(find_duplicates([t((2026, 9, 1), 900), t((2026, 9, 2), 900)])) == 1
    assert find_duplicates([t((2026, 9, 1), 900), t((2026, 9, 3), 900)]) == []


def test_duplicate_needs_same_amount_payee_and_minimum():
    assert find_duplicates([t((2026, 9, 1), 900), t((2026, 9, 1), 901)]) == []
    assert find_duplicates([t((2026, 9, 1), 900), t((2026, 9, 1), 900, key="vpa:other@ybl")]) == []
    assert find_duplicates([t((2026, 9, 1), 20), t((2026, 9, 1), 20)]) == []  # two chais
    assert find_duplicates([t((2026, 9, 1), 900, key=None), t((2026, 9, 1), 900, key=None)]) == []


def test_triplicate_links_each_to_previous():
    a, b, c = (t((2026, 9, 1), 900) for _ in range(3))
    found = find_duplicates([a, b, c])
    assert [(f.transaction_id, f.related_transaction_id) for f in found] == [(b.id, a.id), (c.id, b.id)]


# --- new merchants --------------------------------------------------------------

def test_new_merchant_flagged_once_history_exists():
    old = t((2026, 6, 1), 300, key="vpa:regular@ybl")
    new1 = t((2026, 9, 5), 2500, key="fp:POSHOSTINGCO", name="HostingCo")
    new2 = t((2026, 9, 20), 2500, key="fp:POSHOSTINGCO", name="HostingCo")
    [f] = find_new_merchants([old, new1, new2], history_start=date(2026, 6, 1))
    assert (f.transaction_id, f.dedupe_key) == (new1.id, f"new_merchant:{new1.id}")
    assert f.note == "First payment to HostingCo: ₹2,500.00"


def test_new_merchant_ignored_without_enough_history_or_below_minimum():
    first = t((2026, 9, 5), 2500, key="fp:POSHOSTINGCO")
    assert find_new_merchants([first], history_start=date(2026, 9, 1)) == []  # cold start
    small = t((2026, 9, 5), 150, key="vpa:chai@ybl")
    assert find_new_merchants([small], history_start=date(2026, 1, 1)) == []


# --- recurring price changes ----------------------------------------------------

def subscription(amounts, channel="upi", key="vpa:vpn@ybl", name="ExampleVPN"):
    return [t((2026, 6 + i, 5), a, key=key, channel=channel, name=name, cat="Software & Hosting")
            for i, a in enumerate(amounts)]


def test_price_increase_on_monthly_charge():
    txns = subscription([499, 499, 499, 649])
    [f] = find_price_changes(txns)
    assert (f.transaction_id, f.related_transaction_id) == (txns[3].id, txns[2].id)
    assert f.details["change_pct"] == 30.1
    assert f.note == "ExampleVPN charged ₹649.00, up 30.1% from ₹499.00 on 2026-08-05"


def test_card_charges_get_fx_tolerance():
    assert find_price_changes(subscription([1464.13, 1480.00, 1452.50, 1520.00], channel="pos")) == []
    assert len(find_price_changes(subscription([1464.13, 1464.13, 1464.13, 1700.00], channel="pos"))) == 1


def test_inr_charges_use_tight_tolerance():
    assert len(find_price_changes(subscription([499, 499, 499, 525]))) == 1  # +5.2%
    assert find_price_changes(subscription([499, 499, 499, 505])) == []  # +1.2%


def test_not_recurring_when_too_few_months_or_multiple_per_month():
    assert find_price_changes(subscription([499, 649])) == []
    busy = subscription([499, 499, 499, 649]) + [t((2026, 8, 20), 120, key="vpa:vpn@ybl")]
    assert find_price_changes(busy) == []


# --- monthly category spikes ------------------------------------------------------

def monthly(cat, totals_by_month):
    return [t((2026, m, 10), total, cat=cat, key=f"vpa:{cat}{m}@ybl")
            for m, total in totals_by_month.items() if total]


def test_category_spike():
    txns = monthly("Dining", {3: 4000, 4: 4200, 5: 3800, 6: 4100, 7: 3900, 8: 4000, 9: 8776})
    [f] = find_category_spikes(txns, history_start=date(2026, 3, 1))
    assert (f.anomaly_type, f.category, f.month, f.transaction_id) == (
        "category_spike", "Dining", "2026-09", None)
    assert f.dedupe_key == "category_spike:Dining:2026-09"
    assert f.note == "Dining spend in 2026-09 is ₹8,776.00, 2.2× the usual ₹4,000.00 (median of 6 months)"


def test_no_spike_within_normal_range_or_below_floor():
    normal = monthly("Dining", {6: 4000, 7: 4000, 8: 4000, 9: 5500})
    assert find_category_spikes(normal, history_start=date(2026, 6, 1)) == []
    tiny = monthly("Transport", {6: 100, 7: 100, 8: 100, 9: 1500})
    assert find_category_spikes(tiny, history_start=date(2026, 6, 1)) == []


def test_months_without_spend_count_as_zero():
    # Only one earlier month had any Travel spend: median of (0, 0, 3000) is 0.
    txns = monthly("Travel", {6: 3000, 9: 4000})
    [f] = find_category_spikes(txns, history_start=date(2026, 6, 1))
    assert f.details["baseline_median_paise"] == 0
    assert f.note == "Travel spend in 2026-09 is ₹4,000.00; usually none (median of 3 months is ₹0.00)"


def test_spike_needs_three_months_of_history():
    txns = monthly("Dining", {8: 1000, 9: 9000})
    assert find_category_spikes(txns, history_start=date(2026, 8, 1)) == []
