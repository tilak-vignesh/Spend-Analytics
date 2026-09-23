from app.categorization.keys import merchant_key

VPN_SEP = "POS 416021XXXXXX0000 600000000001 01SEP2\n6 14:26:06 +10000000000 EXAMPLEVPN.COM"
VPN_OCT = "POS 416021XXXXXX0000 600000009999 02OCT26 09:01:02 +10000000000 EXAMPLE\nVPN.COM"


def test_vpa_is_the_key_when_present():
    assert merchant_key("anything", vpa="q000000005@ybl") == "vpa:q000000005@ybl"


def test_fingerprint_ignores_refs_dates_times_and_wraps():
    assert merchant_key(VPN_SEP, vpa=None) == merchant_key(VPN_OCT, vpa=None)


def test_fingerprint_is_readable():
    assert merchant_key(VPN_SEP, vpa=None) == "fp:POS+EXAMPLEVPN.COM"


def test_different_merchants_differ():
    other = VPN_SEP.replace("EXAMPLEVPN.COM", "HOSTINGCO GMBH")
    assert merchant_key(other, vpa=None) != merchant_key(VPN_SEP, vpa=None)


def test_numeric_ids_and_dashes_collapse():
    a = merchant_key("IMPS-600000000004-ACME PAYROLL-HDFC\n0001234", vpa=None)
    b = merchant_key("IMPS-611111111111-ACME PAYROLL-HDFC0009999", vpa=None)
    assert a == b == "fp:IMPS-ACMEPAYROLL-HDFC"


def test_ddmmyy_style_refs_are_dropped():
    a = merchant_key("DC INTL POS TXN MARKUP+ST 010926-EPR2724814419980", vpa=None)
    b = merchant_key("DC INTL POS TXN MARKUP+ST 300826-EPR2725125830625", vpa=None)
    assert a == b
