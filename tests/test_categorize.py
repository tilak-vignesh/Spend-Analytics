import json
from datetime import date

import pytest
from sqlmodel import Session, select

from app.categorization.categorize import categorize
from app.db import make_engine
from app.models import Account, MerchantCategoryMap, Transaction

SHOP = "UPI-FRESH MART-Q000000005@YBL-YESB0YBLUPI-100000000005-UPI"
PERSON = "UPI-RAVI\nKUMAR-9000000000@YBL-SBIN0000001-100000000003-UPI"
QR_OWNER = "UPI-HARI P-PAYTM.S00ABC0@PTY-YESB0MCHUPI-100000000009-UPI"
VPN = "POS 416021XXXXXX0000 600000000001 01SEP2\n6 14:26:06 EXAMPLEVPN.COM"

# What the fake LLM answers, by narration
ANSWERS = {
    SHOP: dict(payee_name="FRESH MART", merchant_normalized="Fresh Mart",
               payee_type="merchant", is_person_name=False, category="Food & Groceries"),
    PERSON: dict(payee_name="RAVI KUMAR", merchant_normalized="Ravi Kumar",
                 payee_type="person", is_person_name=True, category="Other"),
    # LLM says "person", but the MCHUPI IFSC is a strong merchant-QR signal.
    QR_OWNER: dict(payee_name="HARI P", merchant_normalized="Hari P",
                   payee_type="person", is_person_name=True, category="Other"),
    VPN: dict(payee_name="EXAMPLEVPN.COM", merchant_normalized="ExampleVPN",
              payee_type="merchant", is_person_name=False, category="Software & Hosting"),
}


class FakeLLM:
    def __init__(self, fail_narrations=()):
        self.fail = set(fail_narrations)
        self.sent = []

    def __call__(self, *, system, prompt, schema):
        batch = json.loads(prompt.split("PAYEES:\n", 1)[1])
        self.sent.extend(p["narration"] for p in batch)
        if any(p["narration"] in self.fail for p in batch):
            raise RuntimeError("boom")
        return {"items": [dict(id=p["id"], payee_vpa=p["vpa"], confidence=0.9,
                               **ANSWERS[p["narration"]]) for p in batch]}


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    with Session(engine) as s:
        s.add(Account(id=1, name="HDFC Savings 1234"))
        s.commit()
    return engine


_ids = iter(range(1, 10_000))


def add(engine, narration, amount, vpa=None, ifsc=None, **kw):
    i = next(_ids)
    with Session(engine) as s:
        s.add(Transaction(id=i, account_id=1, txn_date=date(2026, 9, 1), amount_paise=amount,
                          dedupe_key=f"k{i}", narration=narration, payee_vpa=vpa,
                          payee_ifsc=ifsc, channel="upi" if vpa else "pos",
                          txn_type="debit" if amount < 0 else "credit", **kw))
        s.commit()
    return i


def get(engine, id):
    with Session(engine) as s:
        return s.get(Transaction, id)


def cache(engine):
    with Session(engine) as s:
        return {m.merchant_key: m for m in s.exec(select(MerchantCategoryMap)).all()}


def test_merchant_categorized_and_cached(engine):
    t = add(engine, SHOP, -2000, vpa="q000000005@ybl", ifsc="YESB0YBLUPI")
    result = categorize(engine, FakeLLM())

    txn = get(engine, t)
    assert (txn.category, txn.category_source) == ("Food & Groceries", "llm")
    assert (txn.merchant_key, txn.merchant_normalized) == ("vpa:q000000005@ybl", "Fresh Mart")
    # q...@ybl is a strong merchant_qr signal, overriding the LLM's "merchant"
    assert txn.payee_type == "merchant_qr"
    entry = cache(engine)["vpa:q000000005@ybl"]
    assert (entry.source, entry.confidence, entry.payee_name) == ("llm", 0.9, "FRESH MART")
    assert (result.new_payees, result.categorized, result.failed) == (1, 1, {})


def test_one_llm_item_per_payee(engine):
    add(engine, SHOP, -2000, vpa="q000000005@ybl")
    add(engine, SHOP.replace("100000000005", "100000000006"), -3000, vpa="q000000005@ybl")
    llm = FakeLLM()
    result = categorize(engine, llm)
    assert len(llm.sent) == 1
    assert result.categorized == 2


def test_pos_uses_fingerprint_key(engine):
    t = add(engine, VPN, -146413)
    categorize(engine, FakeLLM())
    assert get(engine, t).merchant_key == "fp:POSEXAMPLEVPN.COM"
    assert get(engine, t).category == "Software & Hosting"


def test_p2p_debit_defaults_to_food_but_cache_keeps_llm_category(engine):
    t = add(engine, PERSON, -17500, vpa="9000000000@ybl", ifsc="SBIN0000001")
    categorize(engine, FakeLLM())
    txn = get(engine, t)
    assert (txn.category, txn.category_source, txn.payee_type) == (
        "Food & Groceries", "p2p_default", "person")
    assert cache(engine)["vpa:9000000000@ybl"].category == "Other"


def test_merchant_qr_with_owner_name_is_p2p(engine):
    t = add(engine, QR_OWNER, -17500, vpa="paytm.s00abc0@pty", ifsc="YESB0MCHUPI")
    categorize(engine, FakeLLM())
    txn = get(engine, t)
    assert txn.payee_type == "merchant_qr"
    assert (txn.category, txn.category_source) == ("Food & Groceries", "p2p_default")


def test_cached_payees_skip_the_llm(engine):
    add(engine, SHOP, -2000, vpa="q000000005@ybl")
    categorize(engine, FakeLLM())
    t = add(engine, SHOP.replace("100000000005", "100000000007"), -900, vpa="q000000005@ybl")
    llm = FakeLLM()
    result = categorize(engine, llm)
    assert llm.sent == []
    assert get(engine, t).category == "Food & Groceries"
    assert (result.new_payees, result.categorized) == (0, 1)


def test_already_categorized_transactions_are_not_resent(engine):
    add(engine, SHOP, -2000, vpa="q000000005@ybl")
    categorize(engine, FakeLLM())
    llm = FakeLLM()
    assert categorize(engine, llm).categorized == 0
    assert llm.sent == []


def test_manual_transaction_untouched(engine):
    t = add(engine, SHOP, -2000, vpa="q000000005@ybl", category="Health",
            category_source="manual")
    llm = FakeLLM()
    categorize(engine, llm)
    assert (get(engine, t).category, get(engine, t).category_source) == ("Health", "manual")
    assert llm.sent == []


def test_manually_confirmed_payee_applies_as_rule(engine):
    with Session(engine) as s:
        s.add(MerchantCategoryMap(merchant_key="vpa:9000000000@ybl", category="Rent",
                                  source="manual", confidence=1.0, payee_type="person",
                                  is_person_name=1, merchant_normalized="Landlord"))
        s.commit()
    t = add(engine, PERSON, -1500000, vpa="9000000000@ybl")
    categorize(engine, FakeLLM())
    assert (get(engine, t).category, get(engine, t).category_source) == ("Rent", "rule")


def test_failed_payees_stay_pending_and_retry_later(engine):
    shop = add(engine, SHOP, -2000, vpa="q000000005@ybl")
    vpn = add(engine, VPN, -146413)
    result = categorize(engine, FakeLLM(fail_narrations={VPN}), batch_size=1)

    assert get(engine, shop).category == "Food & Groceries"
    assert get(engine, vpn).category is None
    assert "boom" in result.failed["fp:POSEXAMPLEVPN.COM"]

    assert categorize(engine, FakeLLM()).categorized == 1
    assert get(engine, vpn).category == "Software & Hosting"
