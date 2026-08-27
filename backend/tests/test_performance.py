"""§0.3, §7.2-7.4 — the epoch, the equity curve and the return figures."""

import datetime as dt
import io
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures"

#: §0.3 — the fixture's own `ChangeInNAV.startingValue` for its window.
EPOCH_DATE = "2031-05-12"
EPOCH_VALUE = 389.15029091478

#: Figures IBKR publishes for the same window in `ChangeInNAV`, used as the
#: independent check on everything derived here.
IB_ENDING_VALUE = 4350.85747501788
IB_UNREALIZED = 72.82874095
IB_OTHER_FEES = -8.8365


def _set_epoch(client, date: str = EPOCH_DATE, value: float = EPOCH_VALUE):
    return client.put(
        "/api/settings/epoch",
        json={"tracking_start_date": date, "tracking_start_value": value},
    ).json()


# --- The epoch anchor (§0.3) ------------------------------------------------


def test_without_an_epoch_percentages_are_withheld_not_guessed(client_with_flex):
    """§7.1 — "Without that anchor, percentage returns are meaningless."

    The failure this app exists to prevent is a plausible-looking wrong
    percentage, so the absence of an anchor produces no number at all and a
    stated reason.
    """
    body = client_with_flex.get("/api/performance").json()

    assert body["epoch"]["configured"] is False
    assert body["returns"]["available"] is False
    assert body["returns"]["twr"] is None
    assert body["returns"]["xirr"] is None
    assert "withheld rather than computed against an assumed zero" in body["returns"]["reason"]

    # Absolute P&L needs no anchor and is still there.
    assert body["series"], "the curve should still render"
    assert body["series"][-1]["cumulative_net_pnl"] != 0


def test_a_date_without_a_value_is_still_unconfigured(client_with_flex):
    client_with_flex.put("/api/settings/epoch", json={"tracking_start_date": EPOCH_DATE})
    body = client_with_flex.get("/api/settings/epoch").json()
    assert body["configured"] is False
    assert "percentage" in body["note"].lower()


def test_setting_the_epoch_triggers_a_full_rebuild(client_with_flex):
    """§0.3 — "Changing the epoch is a full rebuild, not an incremental
    update." """
    body = _set_epoch(client_with_flex)
    assert body["configured"] is True
    assert body["rebuilt"] is True
    assert "re-derived" in body["rebuild_note"]


def test_moving_the_epoch_forward_drops_earlier_days(client_with_flex):
    _set_epoch(client_with_flex)
    early = client_with_flex.get("/api/performance").json()["series"]

    _set_epoch(client_with_flex, date="2031-07-02")
    later = client_with_flex.get("/api/performance").json()["series"]

    assert len(later) < len(early)
    assert later[0]["trading_day"] >= "2031-07-02"


def test_the_epoch_is_reversible(client_with_flex):
    """Reversibility is the point of the full rebuild; a setting that
    cannot be moved back is a one-way door, not a setting."""
    _set_epoch(client_with_flex)
    original = client_with_flex.get("/api/performance").json()

    _set_epoch(client_with_flex, date="2031-07-16")
    _set_epoch(client_with_flex)
    restored = client_with_flex.get("/api/performance").json()

    assert len(restored["series"]) == len(original["series"])
    assert restored["returns"]["twr"] == pytest.approx(original["returns"]["twr"])


def test_trades_closed_before_the_epoch_are_excluded(client_with_flex):
    """§0.3 — pre-epoch executions are still ingested (they supply cost
    basis) but excluded from every statistic and from the equity curve."""
    _set_epoch(client_with_flex, date="2031-07-16")

    trades = client_with_flex.get("/api/trades").json()["items"]
    pre_epoch = [t for t in trades if t["origin"] == "PRE_EPOCH"]
    assert pre_epoch, "the fixture has trades closing before mid-July"

    # The executions behind them are still there.
    assert client_with_flex.get("/api/executions").json()["total"] == 78

    curve = client_with_flex.get("/api/performance").json()["series"]
    excluded_total = sum(t["net_pnl"] for t in pre_epoch)
    assert curve[-1]["cumulative_net_pnl"] == pytest.approx(
        sum(
            t["net_pnl"]
            for t in trades
            if t["origin"] != "PRE_EPOCH" and t["closed_at"] is not None
        ),
        abs=0.01,
    )
    assert excluded_total != 0


def test_straddling_trades_are_flagged_and_lose_their_duration(client_with_flex):
    """§0.3 — "a hold time that began before you were measuring is not a
    hold time you can learn from"."""
    _set_epoch(client_with_flex, date="2031-07-16")
    trades = client_with_flex.get("/api/trades").json()["items"]
    straddling = [t for t in trades if t["origin"] != "PRE_EPOCH" and t["excluded_from_duration"]]
    assert straddling


# --- The equity curve (§7.2, §7.4) ------------------------------------------


def test_account_value_equals_ibkrs_own_nav(client_with_flex):
    """The strongest check in the app on the account-value pipeline.

    IBKR publishes `endingValue` for the same window, and since §12's marks
    landed the app computes the whole thing independently:

        WITH_INCOME ending value == IBKR's endingValue, to the cent

    Every part of the chain has to be right for that to hold — the epoch
    anchor, deposits, the ACATS transfer valued at its transfer-date mark,
    realized P&L including partial closes, fees, and the unrealized mark on
    the open book. Getting deposits right and transfers wrong would still
    balance the account; this would not.

    Phase 3 could only assert this with IBKR's own unrealized figure added
    back by hand. It no longer needs the hand.
    """
    _set_epoch(client_with_flex)
    body = client_with_flex.get("/api/performance", params={"variant": "WITH_INCOME"}).json()
    ending = body["series"][-1]["value"]

    assert ending == pytest.approx(IB_ENDING_VALUE, abs=0.01)
    # ...and the unrealized component is IB's own, not a recomputation.
    assert body["returns"]["closing_unrealized"] == pytest.approx(IB_UNREALIZED, abs=0.01)


def test_the_base_curve_follows_the_spec_formula(client_with_flex):
    """§7.2 — `starting_balance + Σcash_flow + Σrealized`, with income held
    out so the "+ dividends" overlay is a real alternative."""
    _set_epoch(client_with_flex)
    series = client_with_flex.get("/api/performance").json()["series"]

    realized = sum(s["realized_net"] + s["realized_partial_net"] for s in series)
    flows = sum(s["cash_flow"] for s in series)
    # Unrealized is a level, not a flow: the open book's mark on the last
    # day, not a sum across days. Accumulating it would compound the same
    # open position once for every day it stayed open.
    unrealized = series[-1]["unrealized_pnl"]
    assert series[-1]["account_value"] == pytest.approx(
        EPOCH_VALUE + flows + realized + unrealized, abs=0.01
    )

    income = sum(s["income_cash"] for s in series)
    assert income == pytest.approx(IB_OTHER_FEES, abs=0.01)


def test_realized_from_partial_closes_reaches_the_curve(client_with_flex):
    """§8.1 separates "net closed P&L" from "realized incl. partials".

    A half-closed position has banked cash without producing an outcome.
    Leaving it out understates the account permanently and by a growing
    amount, while counting it as a closed trade would corrupt the win rate.
    """
    _set_epoch(client_with_flex)
    body = client_with_flex.get("/api/performance").json()
    partials = body["returns"]["realized_from_partials"]

    assert partials != 0
    closed_only = sum(s["realized_net"] for s in body["series"])
    assert body["series"][-1]["account_value"] == pytest.approx(
        EPOCH_VALUE
        + sum(s["cash_flow"] for s in body["series"])
        + closed_only
        + partials
        + body["series"][-1]["unrealized_pnl"],
        abs=0.01,
    )


def test_curve_variants_differ_by_exactly_the_income(client_with_flex):
    _set_epoch(client_with_flex)
    flows = client_with_flex.get("/api/performance", params={"variant": "WITH_FLOWS"}).json()
    income = client_with_flex.get("/api/performance", params={"variant": "WITH_INCOME"}).json()

    difference = income["series"][-1]["value"] - flows["series"][-1]["value"]
    assert difference == pytest.approx(IB_OTHER_FEES, abs=0.01)


def test_pnl_only_reports_returns_from_the_account_curve(client_with_flex):
    """A cumulative-P&L curve starts at zero and has no denominator;
    chaining sub-period returns over it yields −100% and nothing else."""
    _set_epoch(client_with_flex)
    body = client_with_flex.get("/api/performance", params={"variant": "PNL_ONLY"}).json()

    assert body["series"][0]["value"] == pytest.approx(
        body["series"][0]["cumulative_net_pnl"]
    )
    assert body["returns"]["basis_variant"] == "WITH_FLOWS"
    assert body["returns"]["twr"] > -1.0
    assert "no starting capital" in body["returns"]["basis_note"]


def test_an_unknown_variant_is_refused(client_with_flex):
    response = client_with_flex.get("/api/performance", params={"variant": "SOMETHING"})
    assert response.status_code == 400


def test_the_curve_covers_every_trading_day_not_just_active_ones(client_with_flex):
    """A series built from activity days alone compresses the x-axis and
    hands Sharpe a return stream with its zeroes deleted."""
    _set_epoch(client_with_flex)
    series = client_with_flex.get("/api/performance").json()["series"]

    flat_days = [s for s in series if s["trade_count"] == 0]
    assert flat_days, "a ten-week window has days with no closes"

    days = [dt.date.fromisoformat(s["trading_day"]) for s in series]
    assert days == sorted(days)
    assert len(set(days)) == len(days)
    # No weekends: the calendar comes from the NYSE trading calendar.
    assert all(d.weekday() < 5 for d in days)


def test_returns_report_their_mark_coverage(client_with_flex):
    """§12 — marks are as dense as the import history, and say so.

    A period with one marked day out of fifty is a realized-only curve with
    one marked point, not a marked curve. Reporting the coverage is what
    stops the second reading.
    """
    _set_epoch(client_with_flex)
    returns = client_with_flex.get("/api/performance").json()["returns"]

    assert returns["excludes_unrealized"] is False
    assert returns["marked_days"] == 1, "the fixture is a single statement"
    assert "1 of" in returns["unrealized_note"]

    # With the open book marked, TWR lands within a fifth of a point of
    # IBKR's own 10.2478% — against 8.52% when the book was carried at
    # zero. The comparable basis is WITH_INCOME, since IBKR's figure has
    # fees in it and the default WITH_FLOWS curve does not; that alone puts
    # the default slightly *above* IBKR's.
    with_income = client_with_flex.get(
        "/api/performance", params={"variant": "WITH_INCOME"}
    ).json()["returns"]
    assert with_income["twr"] == pytest.approx(0.1024784403, abs=0.002)
    assert returns["twr"] > with_income["twr"], "fees are excluded from the default curve"

    # It does not match to the decimal, and cannot: one snapshot cannot
    # reconstruct the daily marked path IBKR chains its figure from. The
    # gap closes as sync history accumulates.
    assert with_income["twr"] != pytest.approx(0.1024784403, abs=1e-6)


def test_a_stale_mark_is_never_carried_forward(client_with_flex):
    """A mark held flat across a week asserts a week of zero market
    movement, which is a claim the data does not support."""
    _set_epoch(client_with_flex)
    series = client_with_flex.get("/api/performance").json()["series"]

    priced = [s for s in series if s["unrealized_priced"]]
    assert len(priced) == 1
    assert all(s["unrealized_pnl"] == 0 for s in series if not s["unrealized_priced"])


def test_all_four_return_measures_are_reported(client_with_flex):
    """§7.3 — TWR as the headline, the others clearly labelled."""
    _set_epoch(client_with_flex)
    returns = client_with_flex.get("/api/performance").json()["returns"]

    for key in ("twr", "xirr", "cagr", "simple_return"):
        assert returns[key] is not None, key
        assert returns[f"{key}_note"], f"{key} must be labelled"

    # XIRR weights the late deposits, so it diverges from TWR — and §7.3
    # says the gap between them is itself informative.
    assert returns["xirr"] != pytest.approx(returns["twr"])


# --- Cash edits move the curve ----------------------------------------------


def test_adding_a_manual_deposit_moves_the_equity_curve(client_with_flex):
    _set_epoch(client_with_flex)
    before = client_with_flex.get("/api/performance").json()["series"][-1]["account_value"]

    client_with_flex.post(
        "/api/cash-transactions",
        json={"date": "2031-07-02", "type": "DEPOSIT", "amount": "1000"},
    )
    after = client_with_flex.get("/api/performance").json()["series"][-1]["account_value"]

    assert after == pytest.approx(before + 1000.0, abs=0.01)


def test_a_deposit_itself_earns_no_return(client_with_flex):
    """The defining property of time-weighting, end to end.

    Money arriving is not performance. Dropping $5,000 into a day on which
    nothing traded must leave the cumulative TWR *through that day*
    untouched, while the naive simple return moves — which is §7.3's whole
    argument for showing both.

    Measured through the deposit day rather than to the end of the period,
    because §7.3's formula puts `F_t` in the denominator: the cash counts
    as available from the start of the day it lands, so it dilutes every
    period *after* it. That dilution is real (see the next test), and
    asserting its absence would be asserting a bug.
    """
    _set_epoch(client_with_flex)
    before = client_with_flex.get("/api/performance").json()

    flat_day = next(
        s for s in before["series"][1:] if s["trade_count"] == 0 and s["cash_flow"] == 0
    )
    index = before["series"].index(flat_day)
    twr_through_day_before = before["series"][index]["twr_cumulative"]

    client_with_flex.post(
        "/api/cash-transactions",
        json={"date": flat_day["trading_day"], "type": "DEPOSIT", "amount": "5000"},
    )
    after = client_with_flex.get("/api/performance").json()
    matching = next(
        s for s in after["series"] if s["trading_day"] == flat_day["trading_day"]
    )

    assert matching["cash_flow"] == pytest.approx(5000.0)
    assert matching["twr_cumulative"] == pytest.approx(twr_through_day_before, abs=1e-9)
    assert after["returns"]["simple_return"] != pytest.approx(
        before["returns"]["simple_return"]
    )


def test_undeployed_cash_dilutes_later_returns(client_with_flex):
    """The other half of the same coin, asserted so the behaviour above is
    not mistaken for a rounding tolerance.

    $5,000 parked mid-period and never traded genuinely lowers the
    percentage return on the period that follows, because the same dollar
    P&L is now earned against a larger base.
    """
    _set_epoch(client_with_flex)
    before = client_with_flex.get("/api/performance").json()["returns"]["twr"]

    client_with_flex.post(
        "/api/cash-transactions",
        json={"date": "2031-07-02", "type": "DEPOSIT", "amount": "5000"},
    )
    after = client_with_flex.get("/api/performance").json()["returns"]["twr"]

    assert after < before


# --- The IBKR cross-check (§0.3) --------------------------------------------


def test_the_ibkr_cross_check_is_captured_on_an_epoch_window_import(client_with_flex):
    """§0.3 — "Display IBKR's figure beside the stored one and flag any
    divergence."

    The fixture's window starts on the epoch date, so importing it after
    the epoch is set should record IBKR's own starting value.
    """
    _set_epoch(client_with_flex, value=1000.0)

    content = (FIXTURES / "fixture-flex.xml").read_bytes()
    client_with_flex.post(
        "/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")}
    )

    epoch = client_with_flex.get("/api/settings/epoch").json()
    assert epoch["tracking_start_value_ibkr"] == pytest.approx(EPOCH_VALUE, abs=0.01)
    # A wrong starting value produces returns that look plausible and are
    # wrong, so the gap is surfaced rather than resolved silently.
    assert epoch["divergence"] == pytest.approx(1000.0 - EPOCH_VALUE, abs=0.01)
