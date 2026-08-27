"""§5.4 / §16.2 — assignment, exercise and expiration, tests 40-49.

Built against synthetic statements (see `synthetic_flex.py`): the account
has never had an assignment, and §16.2 asks for exactly this — "build these
against synthetic fixtures now; validate against a real assignment when one
occurs".
"""

import datetime as dt
import io

import pytest

from tests.synthetic_flex import (
    ACCOUNT,
    ACME_CALL_120,
    ACME_PUT_100,
    ACME_STOCK,
    MES_FUTURE,
    MES_PUT_7000,
    SPX_PUT_5000,
    Fill,
    Lot,
    OpenPositionRow,
    OptionEaeRow,
    Statement,
)

EXPIRY = dt.date(2031, 9, 19)


def _upload(client, statement: Statement, name: str = "synthetic.xml"):
    return client.post(
        "/api/import",
        files={"file": (name, io.BytesIO(statement.as_bytes()), "text/xml")},
    ).json()


def _short_put_assigned(*, disposal: bool = True) -> Statement:
    """Sell an ACME 100 put, get assigned, receive 100 shares, sell them.

    The premium is $300, the shares arrive at the $100 strike, and the
    disposal is at $104 — so $300 of option P&L and $400 of stock P&L, and
    under IB's rolled-basis convention one figure of $700.
    """
    fills = [
        Fill(
            ACME_PUT_100,
            quantity=-1,
            price=3.0,
            when=dt.datetime(2031, 8, 21, 10, 0),
            open_close="O",
            transaction_id="opt-open",
        ),
        # The option closes at 0 on assignment — §5.4 convention (a).
        Fill(
            ACME_PUT_100,
            quantity=1,
            price=0.0,
            when=dt.datetime(2031, 9, 19, 16, 0),
            open_close="C",
            transaction_id="opt-close",
            notes="A",
        ),
        # ...and 100 shares arrive at the strike.
        Fill(
            ACME_STOCK,
            quantity=100,
            price=100.0,
            when=dt.datetime(2031, 9, 19, 16, 0),
            open_close="O",
            transaction_id="stk-open",
            notes="A",
        ),
    ]
    if disposal:
        fills.append(
            Fill(
                ACME_STOCK,
                quantity=-100,
                price=104.0,
                when=dt.datetime(2031, 9, 26, 15, 0),
                open_close="C",
                transaction_id="stk-close",
                # IB's convention (b) figure lives here, on the disposal:
                # the $3 premium is already inside the basis, so IB carries
                # the shares at $97 and books $700 on the sale at $104.
                # This app carries them at the $100 strike and books $400,
                # plus $300 on the option — the same $700, split so option
                # performance stays visible (§5.4).
                lots=[
                    Lot(
                        ACME_STOCK,
                        quantity=-100,
                        cost=9_700.0,
                        fifo_pnl_realized=700.0,
                        open_date=EXPIRY,
                    )
                ],
            )
        )

    return Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 10, 1),
        fills=fills,
        option_events=[
            OptionEaeRow(
                ACME_PUT_100,
                transaction_type="Assignment",
                when=EXPIRY,
                quantity=1,
                # Under convention (b) the option books no separate
                # realized P&L at all — the premium went into the basis.
                realized_pnl=0.0,
                trade_id="opt-close",
            )
        ],
        positions=[]
        if disposal
        else [OpenPositionRow(ACME_STOCK, quantity=100, cost_basis_price=100.0, mark_price=104.0)],
    )


# --- Test 41: short put assigned --------------------------------------------


def test_41_short_put_assigned_opens_long_underlying(client_with_flex):
    """§16.2 test 41 — option closes at 0 with realized P&L = full premium;
    underlying opens **long** at strike, quantity = contracts × multiplier;
    both linked via `related_execution_id`."""
    _upload(client_with_flex, _short_put_assigned())

    events = client_with_flex.get("/api/option-events").json()["items"]
    assignment = next(e for e in events if e["event_type"] == "ASSIGNMENT")

    assert assignment["linked"] is True
    assert assignment["underlying"]["quantity"] == pytest.approx(100.0)
    assert assignment["underlying"]["price"] == pytest.approx(100.0)
    assert assignment["underlying"]["side"] == "LONG"

    trades = client_with_flex.get("/api/trades").json()["items"]
    option_trade = next(t for t in trades if t["symbol"].startswith("ACME  310919P"))
    # Convention (a): the premium is the option's result, visible on its own.
    assert option_trade["net_pnl"] == pytest.approx(300.0, abs=0.01)


def test_41_the_link_is_recorded_in_both_directions(client_with_flex):
    """§5.4 — so a chain can be walked from either end."""
    _upload(client_with_flex, _short_put_assigned())
    executions = {e["broker_trade_id"]: e for e in client_with_flex.get("/api/executions").json()["items"]}

    option_close = executions["opt-close"]
    stock_open = executions["stk-open"]
    assert option_close["related_execution_id"] == stock_open["id"]
    assert stock_open["related_execution_id"] == option_close["id"]


# --- Test 42: short call assigned -------------------------------------------


def test_42_short_call_assigned_opens_short_underlying(client_with_flex):
    statement = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 10, 1),
        fills=[
            Fill(ACME_CALL_120, -1, 2.5, dt.datetime(2031, 8, 21, 10, 0), "O", transaction_id="c-open"),
            Fill(ACME_CALL_120, 1, 0.0, dt.datetime(2031, 9, 19, 16, 0), "C", transaction_id="c-close", notes="A"),
            Fill(ACME_STOCK, -100, 120.0, dt.datetime(2031, 9, 19, 16, 0), "O", transaction_id="s-open", notes="A"),
        ],
        option_events=[
            OptionEaeRow(ACME_CALL_120, "Assignment", EXPIRY, quantity=1, trade_id="c-close")
        ],
        positions=[OpenPositionRow(ACME_STOCK, -100, 120.0, 118.0)],
    )
    _upload(client_with_flex, statement)

    assignment = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "ASSIGNMENT"
    )
    assert assignment["linked"] is True
    assert assignment["underlying"]["side"] == "SHORT"
    assert assignment["underlying"]["quantity"] == pytest.approx(-100.0)


# --- Test 43: long put exercised --------------------------------------------


def test_43_long_put_exercised_opens_short_underlying(client_with_flex):
    statement = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 10, 1),
        fills=[
            Fill(ACME_PUT_100, 1, 3.0, dt.datetime(2031, 8, 21, 10, 0), "O", transaction_id="p-open"),
            Fill(ACME_PUT_100, -1, 0.0, dt.datetime(2031, 9, 19, 16, 0), "C", transaction_id="p-close", notes="Ex"),
            Fill(ACME_STOCK, -100, 100.0, dt.datetime(2031, 9, 19, 16, 0), "O", transaction_id="s-open", notes="Ex"),
        ],
        option_events=[
            OptionEaeRow(ACME_PUT_100, "Exercise", EXPIRY, quantity=-1, trade_id="p-close")
        ],
        positions=[OpenPositionRow(ACME_STOCK, -100, 100.0, 97.0)],
    )
    _upload(client_with_flex, statement)

    exercise = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "EXERCISE"
    )
    assert exercise["linked"] is True
    assert exercise["underlying"]["side"] == "SHORT"


# --- Test 44: futures option ------------------------------------------------


def test_44_futures_option_exercises_into_one_contract_per_option(client_with_flex):
    """§16.2 test 44 — "1 contract per option; multiplier from the
    underlying future, not the option".

    The trap: an MES option's multiplier of 5 prices its premium. Treating
    it as a share count would project a 5-contract futures position from one
    option.
    """
    statement = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 9, 1),
        fills=[
            Fill(MES_PUT_7000, -2, 12.0, dt.datetime(2031, 8, 11, 10, 0), "O", transaction_id="f-open"),
            Fill(MES_PUT_7000, 2, 0.0, dt.datetime(2031, 9, 1, 15, 0), "C", transaction_id="f-close", notes="A"),
            # Two options -> two futures contracts, not ten.
            Fill(MES_FUTURE, 2, 7000.0, dt.datetime(2031, 9, 1, 15, 0), "O", transaction_id="fut-open", notes="A"),
        ],
        option_events=[
            OptionEaeRow(MES_PUT_7000, "Assignment", dt.date(2031, 9, 1), quantity=2, trade_id="f-close")
        ],
        positions=[OpenPositionRow(MES_FUTURE, 2, 7000.0, 7010.0)],
    )
    _upload(client_with_flex, statement)

    assignment = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "ASSIGNMENT"
    )
    assert assignment["linked"] is True
    assert assignment["underlying"]["quantity"] == pytest.approx(2.0)
    assert assignment["underlying"]["side"] == "LONG"


def test_44_projection_helper_agrees(db):
    from app.models.enums import AssetClass
    from app.models.instrument import Instrument
    from app.trades.assignment import projected_underlying_quantity

    fop = Instrument(asset_class=AssetClass.FOP, root_symbol="MES", broker_symbol="X", description="", multiplier=5)
    equity = Instrument(asset_class=AssetClass.OPT, root_symbol="ACME", broker_symbol="Y", description="", multiplier=100)

    assert projected_underlying_quantity(-2, fop) == 2
    assert projected_underlying_quantity(-2, equity) == 200


# --- Test 45: cash-settled index option -------------------------------------


def test_45_cash_settled_index_option_creates_no_position(client_with_flex):
    """§16.2 test 45 — cash adjustment at intrinsic value, **no** position.

    The failure this guards against is linking the event to some unrelated
    fill and inventing a holding that never existed.
    """
    statement = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 10, 1),
        fills=[
            Fill(SPX_PUT_5000, -1, 20.0, dt.datetime(2031, 8, 21, 10, 0), "O", transaction_id="x-open"),
            Fill(SPX_PUT_5000, 1, 0.0, dt.datetime(2031, 9, 19, 16, 0), "C", transaction_id="x-close", notes="A"),
        ],
        option_events=[
            OptionEaeRow(SPX_PUT_5000, "Assignment", EXPIRY, quantity=1, trade_id="x-close")
        ],
    )
    _upload(client_with_flex, statement)

    assignment = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "ASSIGNMENT"
    )
    assert assignment["linked"] is False
    assert assignment["underlying"] is None
    assert "cash" in (assignment["link_note"] or "").lower()

    # No chain either: there is no underlying leg for one to span.
    chains = [
        g for g in client_with_flex.get("/api/groups").json()["items"]
        if g["strategy_type"] == "ASSIGNMENT_CHAIN"
    ]
    assert chains == []


# --- Test 46: chain grouping ------------------------------------------------


def test_46_chain_groups_the_whole_episode(client_with_flex):
    """§16.2 test 46 — spread → assigned leg → underlying → disposal form
    one ASSIGNMENT_CHAIN."""
    _upload(client_with_flex, _short_put_assigned())

    chains = [
        g for g in client_with_flex.get("/api/groups").json()["items"]
        if g["strategy_type"] == "ASSIGNMENT_CHAIN"
    ]
    assert len(chains) == 1
    chain = chains[0]

    # $300 of premium plus $400 on the shares.
    assert chain["net_pnl"] == pytest.approx(700.0, abs=0.01)
    assert chain["assigned"] is True
    assert chain["underlying"] == "ACME"


# --- Test 40: chain reconciliation ------------------------------------------


def test_40_chain_reconciles_against_ibs_rolled_basis(client_with_flex):
    """§16.2 test 40 — the carve-out from test 27, asserted explicitly.

    Per-trade comparison against `fifoPnlRealized` is *expected* to disagree
    on a chain: IB rolls the premium into the stock's basis (convention b)
    and this app keeps them apart (convention a). They must agree once the
    chain is summed, and a widened tolerance would hide real bugs
    everywhere else instead.
    """
    report = _upload(client_with_flex, _short_put_assigned())
    reconciliation = report["assignment_check"]

    assert reconciliation["chains_checked"] == 1
    assert reconciliation["chains_matched"] == 1
    assert reconciliation["passed"] is True
    assert reconciliation["computed_total"] == pytest.approx(700.0, abs=0.01)
    assert reconciliation["ib_total"] == pytest.approx(700.0, abs=0.01)


# --- Test 47: risk figures --------------------------------------------------


def test_47_risk_is_frozen_and_post_assignment_capital_reported(client_with_flex):
    """§16.2 test 47 — `max_risk` frozen at the pre-assignment value,
    `assigned = true`, `post_assignment_capital` from the underlying."""
    _upload(client_with_flex, _short_put_assigned())

    chain = next(
        g for g in client_with_flex.get("/api/groups").json()["items"]
        if g["strategy_type"] == "ASSIGNMENT_CHAIN"
    )

    # 100 shares at the $100 strike is $10,000 of capital committed — far
    # more than the option ever tied up, which is the entire point.
    assert chain["post_assignment_capital"] == pytest.approx(10_000.0, abs=0.01)
    assert chain["return_on_post_assignment_capital"] == pytest.approx(0.07, abs=0.001)
    assert chain["assigned"] is True


# --- Test 48: duration split ------------------------------------------------


def test_48_durations_are_split_with_no_combined_figure(client_with_flex):
    """§16.2 test 48 — option-leg and underlying-holding durations reported
    separately; no combined figure emitted."""
    _upload(client_with_flex, _short_put_assigned())

    chain = next(
        g for g in client_with_flex.get("/api/groups").json()["items"]
        if g["strategy_type"] == "ASSIGNMENT_CHAIN"
    )

    # Option held 20 Aug -> 18 Sep; shares held 18 Sep -> 25 Sep.
    assert chain["option_duration_seconds"] == pytest.approx(29 * 86400, abs=86400)
    assert chain["underlying_duration_seconds"] == pytest.approx(7 * 86400, abs=86400)
    assert "duration_seconds" not in chain, "a combined figure would describe neither leg"


# --- Test 49: late detection ------------------------------------------------


def test_49_an_assignment_in_a_later_statement_still_links(client_with_flex):
    """§16.2 test 49 — §5.4's "assignment appears in the *next* statement".

    The first import has the option closing but no news of why, and no
    shares. The second brings both. The link must form on the second pass
    rather than needing everything in one file.
    """
    first = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 9, 19),
        fills=[
            Fill(ACME_PUT_100, -1, 3.0, dt.datetime(2031, 8, 21, 10, 0), "O", transaction_id="opt-open"),
            Fill(ACME_PUT_100, 1, 0.0, dt.datetime(2031, 9, 19, 16, 0), "C", transaction_id="opt-close", notes="A"),
        ],
    )
    _upload(client_with_flex, first, name="first.xml")

    events = client_with_flex.get("/api/option-events").json()["items"]
    assert not [e for e in events if e["event_type"] == "ASSIGNMENT"]

    _upload(client_with_flex, _short_put_assigned(), name="second.xml")

    assignment = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "ASSIGNMENT"
    )
    assert assignment["linked"] is True
    assert assignment["underlying"]["quantity"] == pytest.approx(100.0)


def test_an_unlinked_assignment_says_why(client_with_flex):
    """The event arrives before the position it created. That is a normal
    state, not an error, and it must read as one."""
    statement = Statement(
        from_date=dt.date(2031, 8, 2),
        to_date=dt.date(2031, 10, 1),
        fills=[
            Fill(ACME_PUT_100, -1, 3.0, dt.datetime(2031, 8, 21, 10, 0), "O", transaction_id="opt-open"),
            Fill(ACME_PUT_100, 1, 0.0, dt.datetime(2031, 9, 19, 16, 0), "C", transaction_id="opt-close", notes="A"),
        ],
        option_events=[
            OptionEaeRow(ACME_PUT_100, "Assignment", EXPIRY, quantity=1, trade_id="opt-close")
        ],
    )
    _upload(client_with_flex, statement)

    assignment = next(
        e for e in client_with_flex.get("/api/option-events").json()["items"]
        if e["event_type"] == "ASSIGNMENT"
    )
    assert assignment["linked"] is False
    assert "next statement" in assignment["link_note"]


# --- The notification (§5.4) ------------------------------------------------


def test_an_assignment_raises_a_banner_until_acknowledged(client_with_flex):
    """§5.4 — "the single highest-value notification in the app; do not bury
    it in a warnings list"."""
    report = _upload(client_with_flex, _short_put_assigned())
    assert report["assignment_alert"]["needs_attention"] == 1

    alerts = client_with_flex.get("/api/option-events/alerts").json()
    assert alerts["needs_attention"] == 1
    event_id = alerts["items"][0]["id"]

    client_with_flex.post(f"/api/option-events/{event_id}/acknowledge")
    assert client_with_flex.get("/api/option-events/alerts").json()["needs_attention"] == 0


def test_acknowledgement_survives_a_resync(client_with_flex):
    """Re-raising a dismissed banner on every nightly sync is how the one
    assignment that matters gets ignored."""
    _upload(client_with_flex, _short_put_assigned())
    alerts = client_with_flex.get("/api/option-events/alerts").json()
    client_with_flex.post(f"/api/option-events/{alerts['items'][0]['id']}/acknowledge")

    _upload(client_with_flex, _short_put_assigned(), name="resync.xml")
    assert client_with_flex.get("/api/option-events/alerts").json()["needs_attention"] == 0


def test_expirations_do_not_raise_a_banner(client_with_flex):
    """An expiry was on the calendar and Expiry Watch already forecast it.

    Bannering all ten of the real fixture's expirations would train the user
    to dismiss the banner on sight.
    """
    alerts = client_with_flex.get("/api/option-events/alerts").json()
    events = client_with_flex.get("/api/option-events").json()

    assert events["total"] == 10
    assert {e["event_type"] for e in events["items"]} == {"EXPIRATION"}
    assert alerts["needs_attention"] == 0


# --- The journaling hook (§5.4) ---------------------------------------------


def test_an_assignment_auto_tags_and_drafts_a_note(client_with_flex):
    """§5.4 — "auto-create a draft note on the chain and auto-apply an
    `assigned` tag", so "how do my assigned positions actually resolve?" is
    a filter rather than a memory exercise."""
    _upload(client_with_flex, _short_put_assigned())

    tags = client_with_flex.get("/api/tags").json()["items"]
    assigned = next(t for t in tags if t["name"] == "assigned")
    assert assigned["system"] is True
    assert assigned["usage_count"] >= 1

    notes = client_with_flex.get("/api/notes", params={"scope": "GROUP"}).json()["items"]
    draft = next(n for n in notes if n["draft"])
    assert "assign" in draft["body_md"].lower()


def test_the_assigned_tag_is_a_chart_dimension(client_with_flex):
    """The point of auto-tagging: assigned positions become filterable."""
    _upload(client_with_flex, _short_put_assigned())

    body = client_with_flex.get(
        "/api/charts", params={"dimension": "tag", "metric": "net_pnl", "mode": "LEG"}
    ).json()
    assert any(b["key"] == "assigned" for b in body["buckets"])


# --- Chains must not double-count (§5.4 / §6.3) ------------------------------


def test_a_chain_does_not_double_count_its_legs(client_with_flex):
    """A chain is a *second view* over trades that already belong to their
    own spreads, not another strategy.

    Every aggregate has to exclude it, or the option leg's P&L lands in the
    totals twice — once under its own strategy type and again under
    ASSIGNMENT_CHAIN. This is the invariant, checked across all four
    aggregates at once.
    """
    before = client_with_flex.get("/api/stats", params={"book": ""}).json()["pnl"]["total_net_pnl"]
    before_leg = client_with_flex.get(
        "/api/charts", params={"dimension": "account", "metric": "net_pnl", "mode": "LEG"}
    ).json()["buckets"][0]["value"]

    _upload(client_with_flex, _short_put_assigned())

    chains = [
        g for g in client_with_flex.get("/api/groups").json()["items"]
        if g["strategy_type"] == "ASSIGNMENT_CHAIN"
    ]
    assert len(chains) == 1, "the chain exists"

    # Strategy-mode totals equal leg-mode totals — §6.4's invariant, which
    # a double-counted chain would break immediately.
    strategy_total = client_with_flex.get(
        "/api/charts", params={"dimension": "account", "metric": "net_pnl", "mode": "STRATEGY"}
    ).json()["buckets"][0]["value"]
    leg_total = client_with_flex.get(
        "/api/charts", params={"dimension": "account", "metric": "net_pnl", "mode": "LEG"}
    ).json()["buckets"][0]["value"]
    assert strategy_total == pytest.approx(leg_total, abs=0.01)

    # The account gained exactly the chain's $700, not $1,400.
    assert leg_total == pytest.approx(before_leg + 700.0, abs=0.01)

    after = client_with_flex.get("/api/stats", params={"book": ""}).json()["pnl"]["total_net_pnl"]
    assert after == pytest.approx(before + 700.0, abs=0.01)


def test_the_chain_is_absent_from_segmented_risk(client_with_flex):
    """§6.3's segments would otherwise report the option leg's risk twice."""
    _upload(client_with_flex, _short_put_assigned())
    segments = client_with_flex.get("/api/stats/risk", params={"book": ""}).json()["segments"]
    assert "ASSIGNMENT_CHAIN" not in {s["strategy_type"] for s in segments}
