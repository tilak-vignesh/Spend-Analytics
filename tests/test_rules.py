import pytest

from app.categorization.rules import P2P_DEFAULT_CATEGORY, assign_category, is_p2p
from app.models import MerchantCategoryMap


def entry(**kw):
    base = dict(merchant_key="vpa:x@ybl", category="Shopping", source="llm",
                payee_type="merchant", is_person_name=0)
    return MerchantCategoryMap(**{**base, **kw})


def test_p2p_default_category_is_food_and_groceries():
    assert P2P_DEFAULT_CATEGORY == "Food & Groceries"


@pytest.mark.parametrize(
    "payee_type, is_person_name, expected",
    [
        ("person", 1, True),
        ("person", 0, True),
        ("merchant_qr", 1, True),  # shop QR under the owner's name
        ("merchant_qr", 0, False),  # shop QR with a shop name
        ("merchant", 1, False),
        (None, 1, False),
    ],
)
def test_is_p2p(payee_type, is_person_name, expected):
    assert is_p2p(entry(payee_type=payee_type, is_person_name=is_person_name)) is expected


def test_llm_category_for_merchants():
    assert assign_category(-5000, entry(category="Dining")) == ("Dining", "llm")


def test_p2p_debit_defaults_to_food():
    person = entry(payee_type="person", is_person_name=1, category="Other")
    assert assign_category(-17500, person) == ("Food & Groceries", "p2p_default")


def test_p2p_credit_keeps_llm_category():
    # Money received from a person isn't grocery spend.
    person = entry(payee_type="person", is_person_name=1, category="Transfers")
    assert assign_category(50000, person) == ("Transfers", "llm")


def test_manually_confirmed_payee_wins_even_for_p2p():
    confirmed = entry(payee_type="person", is_person_name=1, category="Rent", source="manual")
    assert assign_category(-1500000, confirmed) == ("Rent", "rule")
