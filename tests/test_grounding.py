import pytest

from app.chat.grounding import unverified_figures


def check(answer, *results, known=()):
    return unverified_figures(answer, list(results), list(known))


@pytest.mark.parametrize("source", [
    {"rows": [["₹8,775.96"]]},  # formatted by inr()
    {"rows": [[877596]]},  # raw paise
    {"rows": [[8775.96]]},  # rupees computed in SQL
    {"total_spend": "₹8,775.96", "total_spend_paise": 877596},
])
def test_amount_found_in_results(source):
    assert check("You spent ₹8,775.96 on Dining.", source) == []


def test_amount_the_model_computed_itself_is_flagged():
    # Real case: model subtracted ₹22,000 from ₹23,358 on its own.
    results = [{"rows": [["2026-09-06", 2335800]]}, {"rows": [[1200000], [1000000]]}]
    assert check("Total ₹23,358: Precize ₹12,000 and ₹10,000, the remaining ₹1,358 was food.",
                 *results) == ["₹1,358"]


def test_model_side_sums_are_flagged():
    assert check("Together that's ₹22,000.", {"rows": [[12000.0], [10000.0]]}) == ["₹22,000"]


@pytest.mark.parametrize("answer, ok", [
    ("about ₹8,776", True),  # rounding a returned value is formatting, not arithmetic
    ("₹8,780", False),
    ("roughly ₹9k", True),
    ("₹0.09L", True),
    ("₹8.7k", False),
])
def test_rounding_of_a_returned_value(answer, ok):
    assert (check(answer, {"rows": [[877596]]}) == []) is ok


def test_formats_and_signs():
    src = {"rows": [[-5740, 68691991]]}
    assert check("Refund -₹57.40, balance Rs. 6,86,919.91 and INR 57.40", src) == []


@pytest.mark.parametrize("answer, source, ok", [
    ("Dining was 33.3% of spend", {"rows": [[33.33]]}, True),
    ("Dining was 33.3% of spend", {"share": 0.333}, True),
    ("Dining was 12.5% of spend", {"rows": [["12.5%"]]}, True),
    ("Dining was 40% of spend", {"rows": [[33.33]]}, False),
])
def test_percentages(answer, source, ok):
    assert (check(answer, source) == []) is ok


def test_figures_from_question_and_history_are_known():
    assert check("None over ₹500; earlier I said ₹8,775.96.", {"rows": []},
                 known=["Any payments over ₹500?", "You spent ₹8,775.96 on Dining."]) == []


def test_no_figures_and_duplicates():
    assert check("You have no Travel spend this month.", {"rows": []}) == []
    assert check("₹99 then ₹99 again", {"rows": []}) == ["₹99"]


def test_booleans_and_dates_are_not_numbers():
    assert check("₹1", {"flag": True, "date": "2026-09-01"}) == ["₹1"]


@pytest.mark.parametrize("answer", ["higher by 3.9 percentage points", "a 3.9 pp gap",
                                    "up 3.9 percentage point"])
def test_percentage_points_are_checked(answer):
    assert check(answer, {"rows": [[13.3, 9.4]]}) != []  # 13.3 - 9.4 done by the model
    assert check(answer, {"rows": [[3.9]]}) == []
