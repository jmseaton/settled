"""§8 statistics — pure-function tests with hand-checkable inputs."""

import datetime as dt

import pytest

from app.stats.engine import (
    MIN_SAMPLE_TRADING_DAYS,
    DayStat,
    TradeStat,
    compute_pnl_summary,
    compute_ratios,
)


def _trade(net, day, excluded=False):
    closed = dt.datetime(2026, 6, day, 15, 0, tzinfo=dt.timezone.utc)
    return TradeStat(
        net_pnl=net,
        gross_pnl=net,
        commissions=0.0,
        opened_at=closed - dt.timedelta(days=1),
        closed_at=closed,
        trading_day=closed.date(),
        excluded_from_win_rate=excluded,
    )


def _days(values):
    cum = 0.0
    out = []
    for i, v in enumerate(values, start=1):
        cum += v
        out.append(DayStat(trading_day=dt.date(2026, 6, 1) + dt.timedelta(days=i), realized_net=v, realized_gross=v, cumulative_net_pnl=cum))
    return out


def test_win_rate_excludes_breakevens_from_denominator():
    trades = [_trade(100, 1), _trade(-50, 2), _trade(0.0, 3)]
    ratios = compute_ratios(trades, _days([100, -50, 0]))
    assert ratios["win_rate"] == pytest.approx(0.5)


def test_orphan_trades_excluded_from_win_rate():
    trades = [_trade(100, 1), _trade(-50, 2), _trade(9999, 3, excluded=True)]
    ratios = compute_ratios(trades, _days([100, -50, 9999]))
    assert ratios["win_rate"] == pytest.approx(0.5)


def test_profit_factor_and_expectancy():
    trades = [_trade(100, 1), _trade(200, 2), _trade(-50, 3), _trade(-50, 4)]
    ratios = compute_ratios(trades, _days([100, 200, -50, -50]))
    assert ratios["profit_factor"] == pytest.approx(3.0)
    # (0.5 * 150) - (0.5 * 50)
    assert ratios["expectancy"] == pytest.approx(50.0)
    assert ratios["win_loss_ratio"] == pytest.approx(3.0)


def test_ratios_gated_behind_sample_size():
    trades = [_trade(10, d) for d in range(1, 6)]
    ratios = compute_ratios(trades, _days([10] * 5))
    assert ratios["insufficient_sample"] is True
    assert ratios["sharpe"] is None
    assert ratios["sortino"] is None
    assert ratios["calmar"] is None


def test_ratios_computed_once_sample_is_sufficient():
    values = [10, -5] * (MIN_SAMPLE_TRADING_DAYS // 2 + 1)
    trades = [_trade(v, min(i + 1, 28)) for i, v in enumerate(values)]
    ratios = compute_ratios(trades, _days(values))
    assert ratios["insufficient_sample"] is False
    assert ratios["sharpe"] is not None


def test_proxy_metrics_are_labelled():
    """Equity-return ratios computed from trade P&L must say so (§7, §8.2)."""
    ratios = compute_ratios([_trade(10, 1)], _days([10]))
    assert "sharpe" in ratios["proxy_metrics"]
    assert "closed-trade" in ratios["proxy_note"]


def test_max_drawdown_and_extremes():
    days = _days([100, -60, -20, 50])
    summary = compute_pnl_summary([_trade(100, 1), _trade(-60, 2), _trade(-20, 3), _trade(50, 4)], days)
    assert summary["best_trade"] == pytest.approx(100)
    assert summary["worst_trade"] == pytest.approx(-60)
    assert summary["best_day"] == pytest.approx(100)
    assert summary["worst_day"] == pytest.approx(-60)
    # Peak 100 -> trough 20
    ratios = compute_ratios([], days)
    assert ratios["max_drawdown"] == pytest.approx(80.0)


def test_streak_totals():
    trades = [_trade(10, 1), _trade(20, 2), _trade(-5, 3), _trade(-15, 4), _trade(30, 5)]
    summary = compute_pnl_summary(trades, _days([10, 20, -5, -15, 30]))
    assert summary["largest_winning_streak_pnl"] == pytest.approx(30.0)
    assert summary["largest_losing_streak_pnl"] == pytest.approx(-20.0)


def test_empty_input_does_not_crash():
    summary = compute_pnl_summary([], [])
    ratios = compute_ratios([], [])
    assert summary["trade_count"] == 0
    assert ratios["win_rate"] is None
    assert ratios["max_drawdown"] == 0.0
