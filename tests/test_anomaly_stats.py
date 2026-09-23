from datetime import date, timedelta

from app.anomalies.base import Txn
from app.anomalies.stats import find_outliers

_ids = iter(range(1, 100_000))
START = date(2026, 3, 1)


def t(day_offset, rupees, cat="Dining", name="Cafe"):
    return Txn(id=next(_ids), txn_date=START + timedelta(days=day_offset),
               spend_paise=round(rupees * 100), merchant_key=f"vpa:{name}@ybl",
               merchant_name=name, category=cat, channel="upi")


TYPICAL = [320, 350, 400, 280, 390, 360, 310, 340, 370, 330, 300, 380]


def history(amounts=TYPICAL, cat="Dining", start=0):
    return [t(start + 5 * i, a, cat=cat) for i, a in enumerate(amounts)]


def test_large_payment_flagged():
    big = t(100, 9000, name="Fancy Place")
    [f] = find_outliers(history() + [big])
    assert (f.anomaly_type, f.transaction_id, f.dedupe_key) == ("outlier", big.id, f"outlier:{big.id}")
    assert f.details["samples"] == 12 and f.details["median_paise"] == 34500
    assert f.note == ("₹9,000.00 to Fancy Place is unusually large for Dining: "
                      "typically ₹345.00 (median of 12 payments in the previous 6 months)")


def test_ordinary_payment_not_flagged():
    # median ₹690, MAD ₹60: ₹900 is a robust z of ~2.4, under the 3.5 cutoff
    assert find_outliers(history([a * 2 for a in TYPICAL]) + [t(100, 900)]) == []


def test_needs_ten_earlier_payments():
    assert find_outliers(history(TYPICAL[:9]) + [t(100, 9000)]) == []


def test_only_previous_six_months_count():
    old = history(start=0)  # days 0-55
    assert find_outliers(old + [t(0 + 55 + 190, 9000)]) == []


def test_small_amounts_ignored():
    bus = history([14] * 12, cat="Transport")
    assert find_outliers(bus + [t(100, 60, cat="Transport")]) == []


def test_only_unusually_large_not_small():
    assert find_outliers(history() + [t(100, 5)]) == []


def test_other_categories_are_separate():
    assert find_outliers(history(cat="Travel") + [t(100, 9000, cat="Dining")]) == []


def test_identical_history_still_catches_big_jumps():
    same = history([300] * 12)
    assert len(find_outliers(same + [t(100, 3000)])) == 1
    assert find_outliers(same + [t(100, 550)]) == []  # < 2x the usual


def test_later_payments_do_not_count_as_history():
    big = t(10, 9000)
    assert find_outliers([big] + history(start=20)) == []
