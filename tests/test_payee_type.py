import pytest

from app.categorization.payee_type import Signal, resolve_payee_type, vpa_signal


@pytest.mark.parametrize(
    "vpa, ifsc, expected",
    [
        # Merchant QRs that often carry a person's (shop owner's) name
        ("paytm.s00abc0@pty", "YESB0MCHUPI", Signal("merchant_qr", strong=True)),
        ("anything@ybl", "YESB0MCHUPI", Signal("merchant_qr", strong=True)),
        ("q000000005@ybl", "YESB0YBLUPI", Signal("merchant_qr", strong=True)),
        ("paytmqr00abcdef@ptys", "YESB0PTMUPI", Signal("merchant_qr", strong=True)),
        ("amzn0000000001@apl", "UBIN0000001", Signal("merchant_qr", strong=True)),
        ("bharatpe.0000000001@unitype", "UNBA0000001", Signal("merchant_qr", strong=True)),
        ("vyapar.000000000001@hdfcbank", "HDFC0000001", Signal("merchant_qr", strong=True)),
        # Payment-gateway merchants
        ("examplestore.rzp@rxaxis", "UTIB0000100", Signal("merchant", strong=True)),
        ("examplestore.payu@hdfcbank", "HDFC0MERUPI", Signal("merchant", strong=True)),
        ("someone@hdfcbank", "HDFC0MERUPI", Signal("merchant", strong=True)),
        # Personal handles: only a weak hint, people also use them for small shops
        ("9000000000@axl", "KARB0000001", Signal("person", strong=False)),
        ("ravi.kumar12@okaxis", "UTIB0000001", Signal("person", strong=False)),
        ("ravi@oksbi", "SBIN0000001", Signal("person", strong=False)),
        ("ravi@okhdfcbank", "HDFC0000001", Signal("person", strong=False)),
        ("ravi@okicici", "ICIC0000001", Signal("person", strong=False)),
        # Unknown
        ("swiggyupi@axisbank", "UTIB0000001", None),
        (None, None, None),
    ],
)
def test_vpa_signal(vpa, ifsc, expected):
    assert vpa_signal(vpa, ifsc) == expected


def test_vpa_signal_is_case_insensitive():
    assert vpa_signal("Q000000005@YBL", None) == Signal("merchant_qr", strong=True)


def test_strong_signal_overrides_llm():
    assert resolve_payee_type("person", Signal("merchant_qr", strong=True)) == "merchant_qr"


def test_llm_overrides_weak_signal():
    assert resolve_payee_type("merchant", Signal("person", strong=False)) == "merchant"


def test_weak_signal_fills_gap():
    assert resolve_payee_type(None, Signal("person", strong=False)) == "person"


def test_llm_alone_and_nothing():
    assert resolve_payee_type("merchant", None) == "merchant"
    assert resolve_payee_type(None, None) is None
