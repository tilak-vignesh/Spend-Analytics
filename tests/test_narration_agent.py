import json

import pytest

from app.ingestion.narration_agent import PayeeInput, PayeeResult, classify_payees

CATEGORIES = ["Food & Groceries", "Dining", "Software & Hosting", "Health", "Income", "Other"]


def inp(key, narration, vpa=None, ifsc=None, channel="upi", direction="debit"):
    return PayeeInput(key=key, narration=narration, channel=channel, vpa=vpa, ifsc=ifsc,
                      direction=direction)


STORE = inp("vpa:teststoreqr12@okaxis",
            "UPI-TEST\nSTORE-TESTSTOREQR12@OKA\nXIS-UBIN09167\n81-100000000002-UPI",
            vpa="teststoreqr12@okaxis", ifsc="UBIN0916781")
VPN = inp("fp:POS+EXAMPLEVPN.COM",
          "POS 416021XXXXXX0000 600000000001 01SEP2\n6 14:26:06 EXAMPLEVPN.COM", channel="pos")


def item(id, **overrides):
    base = dict(id=id, payee_name="Test Store", payee_vpa=None, merchant_normalized="Test Store",
                payee_type="merchant", is_person_name=False, category="Food & Groceries",
                confidence=0.9)
    return {**base, **overrides}


class FakeLLM:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def __call__(self, *, system, prompt, schema):
        self.calls.append(dict(system=system, prompt=prompt, schema=schema))
        batch = json.loads(prompt.split("PAYEES:\n", 1)[1])
        return self.respond(batch)


def echo(**overrides):
    return FakeLLM(lambda batch: {"items": [item(p["id"], **overrides) for p in batch]})


def test_maps_results_back_to_keys():
    llm = FakeLLM(lambda batch: {"items": [
        item(0, payee_vpa="teststoreqr12@okaxis"),
        item(1, payee_name="EXAMPLEVPN.COM", merchant_normalized="ExampleVPN",
             category="Software & Hosting", confidence=0.95),
    ]})
    out = classify_payees([STORE, VPN], CATEGORIES, llm)

    assert out.failed == {}
    assert out.results[STORE.key] == PayeeResult(
        payee_name="Test Store", payee_vpa="teststoreqr12@okaxis",
        merchant_normalized="Test Store", payee_type="merchant", is_person_name=False,
        category="Food & Groceries", confidence=0.9)
    assert out.results[VPN.key].merchant_normalized == "ExampleVPN"


def test_prompt_and_schema():
    llm = echo()
    classify_payees([STORE], CATEGORIES, llm)
    [call] = llm.calls

    payees = json.loads(call["prompt"].split("PAYEES:\n", 1)[1])
    assert payees == [{"id": 0, "narration": STORE.narration, "channel": "upi",
                       "vpa": "teststoreqr12@okaxis", "ifsc": "UBIN0916781",
                       "direction": "debit"}]
    assert "line wrap" in call["system"]  # model is told \n may or may not be a space
    props = call["schema"]["properties"]["items"]["items"]["properties"]
    assert props["category"]["enum"] == CATEGORIES
    assert props["payee_type"]["enum"] == ["merchant", "merchant_qr", "person"]


def test_no_amounts_or_balances_sent():
    llm = echo()
    classify_payees([STORE], CATEGORIES, llm)
    assert "amount" not in llm.calls[0]["prompt"] and "balance" not in llm.calls[0]["prompt"]


def test_batches():
    inputs = [inp(f"vpa:s{i}@ybl", f"UPI-S{i}-S{i}@YBL", vpa=f"s{i}@ybl") for i in range(120)]
    llm = echo()
    out = classify_payees(inputs, CATEGORIES, llm, batch_size=50)
    assert [len(json.loads(c["prompt"].split("PAYEES:\n", 1)[1])) for c in llm.calls] == [50, 50, 20]
    assert len(out.results) == 120


def test_empty_input_makes_no_call():
    llm = echo()
    assert classify_payees([], CATEGORIES, llm).results == {}
    assert llm.calls == []


def test_unknown_category_is_rejected():
    out = classify_payees([STORE], CATEGORIES, echo(category="Groceries & Stuff"))
    assert STORE.key not in out.results
    assert "category" in out.failed[STORE.key]


def test_invented_vpa_is_dropped_but_item_kept():
    out = classify_payees([STORE], CATEGORIES, echo(payee_vpa="madeup@ybl"))
    assert out.results[STORE.key].payee_vpa is None


def test_vpa_split_by_wrap_is_accepted():
    out = classify_payees([STORE], CATEGORIES, echo(payee_vpa="TestStoreQR12@okaxis"))
    assert out.results[STORE.key].payee_vpa == "teststoreqr12@okaxis"


def test_invalid_payee_type_becomes_none_and_confidence_is_clamped():
    out = classify_payees([STORE], CATEGORIES, echo(payee_type="restaurant", confidence=7))
    assert out.results[STORE.key].payee_type is None
    assert out.results[STORE.key].confidence == 1.0


def test_missing_and_unknown_ids():
    llm = FakeLLM(lambda batch: {"items": [item(0), item(99)]})
    out = classify_payees([STORE, VPN], CATEGORIES, llm)
    assert set(out.results) == {STORE.key}
    assert out.failed == {VPN.key: "no result returned"}


def test_blank_merchant_name_is_rejected():
    out = classify_payees([STORE], CATEGORIES, echo(merchant_normalized="  "))
    assert STORE.key in out.failed


@pytest.mark.parametrize("response", [None, [], {"items": "nope"}, {"nothing": []}])
def test_malformed_response_fails_batch(response):
    out = classify_payees([STORE], CATEGORIES, FakeLLM(lambda batch: response))
    assert out.results == {} and STORE.key in out.failed


def test_llm_error_fails_only_that_batch():
    def respond(batch):
        if batch[0]["narration"] == STORE.narration:
            raise RuntimeError("503 overloaded")
        return {"items": [item(p["id"]) for p in batch]}

    out = classify_payees([STORE, VPN], CATEGORIES, FakeLLM(respond), batch_size=1)
    assert set(out.results) == {VPN.key}
    assert "503 overloaded" in out.failed[STORE.key]
