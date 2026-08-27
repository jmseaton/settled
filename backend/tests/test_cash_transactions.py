"""§7.1 / §4.7 — cash transactions from Flex and by hand."""

import datetime as dt

import pytest

from app.cash.manual import create_transaction, import_csv, parse_amount, parse_date, parse_type
from app.cash.manual import CashInputError
from app.models.cash_transaction import (
    CashSource,
    CashTransaction,
    CashTransactionType,
    classify_ib_cash_type,
)

ACCOUNT = "U0000000"


# --- IB type mapping (§7.1) -------------------------------------------------


@pytest.mark.parametrize(
    "raw,amount,expected",
    [
        ("Deposits/Withdrawals", 300.0, CashTransactionType.DEPOSIT),
        ("Deposits/Withdrawals", -117.7125, CashTransactionType.WITHDRAWAL),
        ("Dividends", 12.0, CashTransactionType.DIVIDEND),
        ("Payment In Lieu Of Dividends", 3.0, CashTransactionType.DIVIDEND),
        ("Broker Interest Received", 1.5, CashTransactionType.INTEREST),
        ("Other Fees", -10.0, CashTransactionType.FEE),
        ("Withholding Tax", -1.0, CashTransactionType.FEE),
    ],
)
def test_ib_types_map_onto_the_enum(raw, amount, expected):
    kind, recognized = classify_ib_cash_type(raw, amount)
    assert kind is expected
    assert recognized is True


def test_one_ib_label_covers_both_directions():
    """IB files deposits and withdrawals under a single label, so the sign
    is what distinguishes them — not the label."""
    assert classify_ib_cash_type("Deposits/Withdrawals", 100)[0] is CashTransactionType.DEPOSIT
    assert classify_ib_cash_type("Deposits/Withdrawals", -100)[0] is CashTransactionType.WITHDRAWAL


def test_unrecognized_type_becomes_adjustment_and_says_so():
    """§7.1 — an unmapped type is not an error, but it must be visible."""
    kind, recognized = classify_ib_cash_type("Carbon Credits", -5.0)
    assert kind is CashTransactionType.ADJUSTMENT
    assert recognized is False


def test_type_matching_survives_capitalisation_changes():
    assert classify_ib_cash_type("  DEPOSITS/WITHDRAWALS ", 1)[1] is True


# --- Flex ingestion ---------------------------------------------------------


def test_flex_cash_reconciles_against_change_in_nav(client_with_flex):
    """The strongest available check on the cash pipeline.

    `ChangeInNAV` publishes IB's own totals for the same period, so the
    parsed rows can be summed against them. Getting deposits right and
    transfers wrong would still balance the account; these three assertions
    would not.
    """
    body = client_with_flex.get("/api/cash-transactions").json()
    totals = body["totals_by_type"]

    deposits = totals["DEPOSIT"] + totals["WITHDRAWAL"]
    assert deposits == pytest.approx(969.65, abs=0.01), "ChangeInNAV.depositsWithdrawals"
    assert totals["TRANSFER_IN"] == pytest.approx(2598.275, abs=0.01), "ChangeInNAV.assetTransfers"
    assert totals["FEE"] == pytest.approx(-8.8365, abs=0.01), "ChangeInNAV.otherFees"


def test_transfers_become_external_flows(client_with_flex):
    """§4.7 — a position arriving by ACATS is money entering the account.

    Counted as trading P&L it would read as a windfall gain; as an external
    flow, TWR strips it out, which is the correct treatment.
    """
    items = client_with_flex.get("/api/cash-transactions").json()["items"]
    transfers = [i for i in items if i["type"] == "TRANSFER_IN"]
    assert len(transfers) == 1
    assert transfers[0]["is_external_flow"] is True
    assert transfers[0]["amount"] == pytest.approx(2598.275, abs=0.01)


def test_reimport_does_not_duplicate_cash(client_with_flex, complete_bytes):
    """A resync restates its window rather than accumulating it."""
    import io
    import pathlib

    before = client_with_flex.get("/api/cash-transactions").json()
    content = (pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "fixture-flex.xml").read_bytes()
    client_with_flex.post(
        "/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")}
    )
    after = client_with_flex.get("/api/cash-transactions").json()

    assert after["total"] == before["total"]
    assert after["net_external_flow"] == pytest.approx(before["net_external_flow"])


def test_manual_rows_survive_a_resync(client_with_flex):
    """§7.1 — the entire reason `source` exists.

    A correction a nightly sync silently reverts is worse than no
    correction, because you stop checking.
    """
    import io
    import pathlib

    created = client_with_flex.post(
        "/api/cash-transactions",
        json={"date": "2031-06-02", "type": "DEPOSIT", "amount": "500", "note": "pre-Flex wire"},
    ).json()
    assert created["source"] == "MANUAL"

    content = (pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "fixture-flex.xml").read_bytes()
    client_with_flex.post(
        "/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")}
    )

    items = client_with_flex.get("/api/cash-transactions", params={"source": "MANUAL"}).json()
    assert [i["note"] for i in items["items"]] == ["pre-Flex wire"]


def test_flex_rows_refuse_in_place_edits(client_with_flex):
    """Editing a broker row would be undone by the next sync without a word."""
    items = client_with_flex.get("/api/cash-transactions", params={"source": "FLEX"}).json()["items"]
    target = items[0]["id"]

    response = client_with_flex.put(f"/api/cash-transactions/{target}", json={"amount": "1"})
    assert response.status_code == 409
    assert "manual" in response.json()["detail"].lower()

    assert client_with_flex.delete(f"/api/cash-transactions/{target}").status_code == 409


# --- Manual entry -----------------------------------------------------------


def test_withdrawal_sign_is_normalized(db):
    """A user who types `500` for a withdrawal and one who types `-500` mean
    the same thing; the equity curve must not move in opposite directions."""
    positive = create_transaction(
        db, ACCOUNT, occurred_on=dt.date(2031, 6, 2), kind=CashTransactionType.WITHDRAWAL, amount=500
    )
    negative = create_transaction(
        db, ACCOUNT, occurred_on=dt.date(2031, 6, 2), kind=CashTransactionType.WITHDRAWAL, amount=-500
    )
    assert float(positive.amount) == -500.0
    assert float(negative.amount) == -500.0


def test_csv_paste_imports_rows_and_names_the_bad_one(db):
    """A paste of many rows with one typo imports the rest and reports the
    typo — rejecting the lot makes the typo harder to find, not easier."""
    result = import_csv(
        db,
        ACCOUNT,
        "date,type,amount,note\n"
        "2031-01-06,DEPOSIT,1000,opening wire\n"
        "2031-02-12,WITHDRAWAL,(250),partial\n"
        "not-a-date,DEPOSIT,100,broken\n"
        "2031-03-02,DIVIDEND,4.25,\n",
    )
    assert result.inserted == 3
    assert result.rows_read == 4
    assert len(result.errors) == 1
    assert "Line 4" in result.errors[0]

    rows = db.query(CashTransaction).filter(CashTransaction.account_id == ACCOUNT).all()
    assert all(r.source == CashSource.MANUAL for r in rows)
    assert sorted(float(r.amount) for r in rows) == [-250.0, 4.25, 1000.0]


def test_csv_missing_column_is_refused_by_name(db):
    result = import_csv(db, ACCOUNT, "date,amount\n2026-01-05,100\n")
    assert result.inserted == 0
    assert "type" in result.errors[0]


def test_amount_parsing_accepts_the_formats_people_paste():
    assert parse_amount("$1,234.56") == pytest.approx(1234.56)
    assert parse_amount("(250)") == pytest.approx(-250.0)
    assert parse_amount("-12.5") == pytest.approx(-12.5)
    with pytest.raises(CashInputError):
        parse_amount("twelve")


def test_date_parsing_accepts_ib_and_iso_forms():
    assert parse_date("2031-06-25") == dt.date(2031, 6, 25)
    assert parse_date("20310625") == dt.date(2031, 6, 25)
    with pytest.raises(CashInputError):
        parse_date("June 24th")


def test_unknown_manual_type_lists_the_valid_ones():
    with pytest.raises(CashInputError) as exc:
        parse_type("REFUND")
    assert "DEPOSIT" in str(exc.value)
