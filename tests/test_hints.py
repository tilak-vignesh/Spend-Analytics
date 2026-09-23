import pytest

from app.ingestion.hints import Hints, extract_hints


@pytest.mark.parametrize(
    "narration, channel",
    [
        ("UPI-FRESH MART-Q000000005@YBL-YESB0YBLUPI-100000000005-UPI", "upi"),
        ("POS 416021XXXXXX0000 600000000001 01SEP2\n6 14:26:06 EXAMPLEVPN.COM", "pos"),
        ("NEFT CR-ICIC0000001-ACME PAYROLL", "neft"),
        ("IMPS-600000000004-ACME PAYROLL", "imps"),
        ("NWD-416021XXXXXX0000-ATM0001-BANGALORE", "atm"),
        ("ATW-416021XXXXXX0000-ATM0001-BANGALORE", "atm"),
        ("ACH D- EXAMPLE MUTUAL FUND-0000001", "other"),
        ("DC INTL POS TXN MARKUP+ST 010926", "other"),
        ("CREDIT INTEREST CAPITALISED", "other"),
    ],
)
def test_channel(narration, channel):
    assert extract_hints(narration).channel == channel


def test_upi_vpa_and_ifsc_on_one_line():
    assert extract_hints(
        "UPI-FRESH MART-Q000000005@YBL-YESB0YBLUPI-100000000005-UPI"
    ) == Hints(channel="upi", vpa="q000000005@ybl", ifsc="YESB0YBLUPI")


def test_tokens_split_by_a_line_wrap_are_rejoined():
    # Wraps land mid-token: inside the IFSC, and inside the VPA handle.
    hints = extract_hints(
        "UPI-RAVI KUMAR D\nS-9000000000@AXL-KA\nRB0000001-100000000003-UPI"
    )
    assert hints.vpa == "9000000000@axl"
    assert hints.ifsc == "KARB0000001"

    hints = extract_hints("UPI-TEST\nSTORE-TESTSTOREQR12@OKA\nXIS-UBIN09167\n81-100000000009-UPI")
    assert hints.vpa == "teststoreqr12@okaxis"
    assert hints.ifsc == "UBIN0916781"


def test_payee_name_hyphens_do_not_leak_into_vpa():
    hints = extract_hints("UPI-JOHN-DOE-john.doe_1@okicici-ICIC0000001-100000000010-UPI")
    assert hints.vpa == "john.doe_1@okicici"


def test_ifsc_is_taken_after_the_vpa():
    # A payee name that happens to look like an IFSC must not win.
    hints = extract_hints("UPI-ABCD0EFGHIJ-shop@ybl-YESB0YBLUPI-100000000011-UPI")
    assert hints.ifsc == "YESB0YBLUPI"


def test_no_hints_outside_upi():
    assert extract_hints("POS 416021XXXXXX0000 600000000001 SUPPORT@EXAMPLE.COM") == Hints(
        channel="pos", vpa=None, ifsc=None
    )


def test_upi_without_recognisable_vpa():
    assert extract_hints("UPI-SOMETHING ODD-100000000012") == Hints(
        channel="upi", vpa=None, ifsc=None
    )
