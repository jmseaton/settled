"""§2 parser behaviour: omitted sections, flag sets, privacy, symbol schemes."""

import datetime as dt

import pytest

from app.models.enums import AssetClass, Right
from app.parsers.symbols import parse_equity_option_symbol, parse_futures_option_symbol
from app.parsers.tlg import TlgParseError, parse_tlg

MINIMAL = """ACCOUNT_INFORMATION
ACT_INF|U0000000|Test Account Holder|Individual|1 Test Street  Anytown PA 00000 United States

STOCK_TRANSACTIONS
STK_TRD|1|AAA|AAA CORP|NYSE|BUYTOOPEN|O|20310602|10:00:00|USD|10.00|1.00|5.00|50.00|-1.00|1.00

EOF
"""


def test_omitted_sections_are_not_assumed():
    """§2.1 — sections are omitted entirely when empty; the parser must cope."""
    result = parse_tlg(MINIMAL)
    assert result.sections_seen == {"ACCOUNT_INFORMATION", "STOCK_TRANSACTIONS"}
    assert result.positions == []
    assert len(result.executions) == 1


def test_account_name_and_address_are_not_carried_past_the_parser():
    """§2.1 privacy note — parse the account ID for scoping; do not persist
    the holder's legal name or mailing address."""
    result = parse_tlg(MINIMAL)
    assert result.account.account_id == "U0000000"
    assert not hasattr(result.account, "name")
    assert not hasattr(result.account, "address")

    serialized = repr(result.account)
    assert "Test Account Holder" not in serialized
    assert "Anytown" not in serialized


def test_unknown_flag_warns_but_imports():
    """§2.3 — unknown flags log a warning and do not fail the import."""
    text = MINIMAL.replace("|O|20310602|", "|O;ZZ|20310602|")
    result = parse_tlg(text)
    assert len(result.executions) == 1
    assert result.executions[0].flags == ["O", "ZZ"]
    assert any("ZZ" in w for w in result.warnings)


def test_flags_parsed_as_a_set_not_a_string():
    text = MINIMAL.replace("|O|20310602|", "|C;Ep|20310602|")
    result = parse_tlg(text)
    assert result.executions[0].flags == ["C", "Ep"]


def test_malformed_row_raises():
    text = MINIMAL.replace(
        "STK_TRD|1|AAA|AAA CORP|NYSE|BUYTOOPEN|O|20310602|10:00:00|USD|10.00|1.00|5.00|50.00|-1.00|1.00",
        "STK_TRD|1|AAA|too|few",
    )
    with pytest.raises(TlgParseError):
        parse_tlg(text)


def test_proceeds_identity_violation_warns():
    text = MINIMAL.replace("|50.00|-1.00|1.00", "|999.00|-1.00|1.00")
    result = parse_tlg(text)
    assert any("proceeds" in w for w in result.warnings)


def test_occ_option_symbol_parsing():
    parsed = parse_equity_option_symbol("ACME  310711P00187050", "ACME 11JUL31 187.05 P")
    assert parsed.asset_class == AssetClass.OPT
    assert parsed.root_symbol == "ACME"
    assert parsed.expiry == dt.date(2031, 7, 11)
    assert parsed.right == Right.PUT
    assert parsed.strike == pytest.approx(187.05)
    assert parsed.warning is None


def test_occ_fractional_strike():
    parsed = parse_equity_option_symbol("MEMC  310711P00021285", "MEMC 11JUL31 21.285 P")
    assert parsed.strike == pytest.approx(21.285)


def test_occ_description_disagreement_is_flagged_not_silently_trusted():
    """§2.4 — cross-check against the description and log a discrepancy."""
    parsed = parse_equity_option_symbol("ACME  310711P00187050", "ACME 11JUL31 429.57 P")
    assert parsed.warning is not None
    assert "disagrees" in parsed.warning


def test_futures_option_uses_description_not_local_symbol():
    """§2.4 — the local symbol is not reliably parseable; the description is
    the `.tlg` fallback."""
    parsed = parse_futures_option_symbol("EX1Q1 P3061.6", "MES 08AUG31 3061.6 P")
    assert parsed.asset_class == AssetClass.FOP
    assert parsed.root_symbol == "MES"
    assert parsed.broker_local_symbol == "EX1Q1 P3061.6"
    assert parsed.expiry == dt.date(2031, 8, 8)
    assert parsed.strike == pytest.approx(3061.6)
    assert parsed.right == Right.PUT


def test_unparseable_futures_option_is_surfaced_not_dropped():
    parsed = parse_futures_option_symbol("ZZZZ P1", "garbage description")
    assert parsed.parse_status == "unparsed"
    assert parsed.warning is not None
