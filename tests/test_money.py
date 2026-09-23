import pytest

from app.money import format_inr, parse_paise


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,464.13", 146413),
        ("686,919.91", 68691991),
        ("57.40", 5740),
        ("57.4", 5740),
        ("20", 2000),
        ("  112.00 ", 11200),
        ("0.05", 5),
        ("-1,208.84", -120884),
        # Values where float arithmetic would drift
        ("0.29", 29),
        ("1,00,000.07", 10000007),
    ],
)
def test_parse_paise(text, expected):
    assert parse_paise(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "abc", "1.234", "1.2.3", "₹100", "--5"])
def test_parse_paise_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_paise(text)


@pytest.mark.parametrize(
    "paise, expected",
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (2000, "₹20.00"),
        (146413, "₹1,464.13"),
        (68691991, "₹6,86,919.91"),
        (1234567890, "₹1,23,45,678.90"),
        (-120884, "-₹1,208.84"),
    ],
)
def test_format_inr(paise, expected):
    assert format_inr(paise) == expected


def test_round_trip():
    for text in ["1,464.13", "6,86,919.91", "0.05"]:
        assert format_inr(parse_paise(text)) == f"₹{text}"
