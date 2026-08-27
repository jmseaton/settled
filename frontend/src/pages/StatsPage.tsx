import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { api, type EquityPoint, type Stats } from '../api'
import { money, num, pct, signClass } from '../format'

function Stat({ label, value, cls, note }: { label: string; value: string; cls?: string; note?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value ${cls ?? ''}`}>{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  )
}

export function StatsPage({ mode }: { mode: string }) {
  const [book, setBook] = useState('Options Income')
  const [stats, setStats] = useState<Stats | null>(null)
  const [curve, setCurve] = useState<EquityPoint[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.stats(book, mode).then(setStats).catch((e) => setError(String(e)))
  }, [book, mode])

  useEffect(() => {
    api.equityCurve().then((r) => setCurve(r.items)).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="banner err">{error}</div>
  if (!stats) return <p className="muted">Loading…</p>

  const p = stats.pnl
  const r = stats.ratios
  const gated = r.insufficient_sample

  const chartOption = {
    backgroundColor: 'transparent',
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: curve.map((c) => c.trading_day),
      axisLine: { lineStyle: { color: '#2a2f3a' } },
      axisLabel: { color: '#98a0ae' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#98a0ae', formatter: (v: number) => `$${v.toFixed(0)}` },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        type: 'line',
        data: curve.map((c) => c.cumulative_net_pnl),
        smooth: false,
        showSymbol: false,
        lineStyle: { color: '#58a6ff', width: 2 },
        areaStyle: { color: 'rgba(88,166,255,0.12)' },
      },
    ],
  }

  return (
    <>
      <h1>Statistics</h1>
      <p className="subtitle">
        Book: <strong>{stats.book}</strong> · Analyzing by{' '}
        <strong>{stats.analysis_mode === 'STRATEGY' ? 'strategy' : 'leg'}</strong> over{' '}
        {stats.unit_count} units · Transfer basis policy: <strong>TRANSFER_MARK</strong>{' '}
        (default). All three silently change what these numbers mean.
      </p>
      <p className="subtitle">
        {stats.analysis_mode_note} Totals are identical in either mode; win rate, expectancy,
        counts and duration are not.
      </p>

      <div className="toolbar">
        <select value={book} onChange={(e) => setBook(e.target.value)}>
          <option value="Options Income">Options Income</option>
          <option value="Holdings">Holdings</option>
          <option value="">All books (pooled)</option>
        </select>
        {book === '' && (
          <span className="muted">
            Pooled view — a buy-and-hold position can dominate options statistics here.
          </span>
        )}
      </div>

      <h2>Cumulative net P&amp;L</h2>
      <div className="panel">
        {curve.length > 0
          ? <ReactECharts option={chartOption} style={{ height: 300 }} theme="dark" />
          : <p className="muted">No closed trades yet.</p>}
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          Realized P&amp;L across all books — what trading produced, with no cash flows in it. For
          the account equity curve, TWR and the benchmark comparison, see <strong>Performance</strong>.
        </div>
      </div>

      <h2>P&amp;L</h2>
      <div className="stat-grid">
        <Stat label="Total net P&L" value={money(p.total_net_pnl)} cls={signClass(p.total_net_pnl)} />
        <Stat label="Total gross P&L" value={money(p.total_gross_pnl)} cls={signClass(p.total_gross_pnl)} />
        <Stat label="Closed trades" value={String(p.trade_count)} />
        <Stat label="Avg per trade" value={money(p.avg_pnl_per_trade)} cls={signClass(p.avg_pnl_per_trade)} />
        <Stat label="Avg winner" value={money(p.avg_win)} cls="pos" />
        <Stat label="Avg loser" value={money(p.avg_loss)} cls="neg" />
        <Stat label="Best trade" value={money(p.best_trade)} cls="pos" />
        <Stat label="Worst trade" value={money(p.worst_trade)} cls="neg" />
        <Stat label="Best day" value={money(p.best_day)} cls="pos" />
        <Stat label="Worst day" value={money(p.worst_day)} cls="neg" />
        <Stat label="Sum of winners" value={money(p.sum_winners)} cls="pos" />
        <Stat label="Sum of losers" value={money(p.sum_losers)} cls="neg" />
      </div>

      <h2>Ratios</h2>
      <div className="stat-grid">
        <Stat label="Win rate" value={pct(r.win_rate)} />
        <Stat label="Profit factor" value={num(r.profit_factor)} />
        <Stat label="Win/loss ratio" value={num(r.win_loss_ratio)} />
        <Stat label="Expectancy" value={money(r.expectancy)} cls={signClass(r.expectancy)} />
        <Stat label="Kelly criterion" value={pct(r.kelly)} />
        <Stat label="SQN" value={num(r.sqn)} />
        <Stat label="Max drawdown" value={money(r.max_drawdown)} cls="neg" />
      </div>

      <h2>Risk-adjusted ratios</h2>
      {gated ? (
        <div className="banner warn">
          <strong>Insufficient data.</strong> Sharpe, Sortino, Calmar, Omega, recovery factor and
          the Ulcer index need at least {r.min_sample_trading_days} trading days to mean anything.
          This data has {r.sample_trading_days}. Showing a number here would be noise dressed up as
          a measurement.
        </div>
      ) : (
        <>
          <div className="stat-grid">
            <Stat label="Sharpe" value={num(r.sharpe)} />
            <Stat label="Sortino" value={num(r.sortino)} />
            <Stat label="Calmar" value={num(r.calmar)} />
            <Stat label="Omega" value={num(r.omega)} />
            <Stat label="Recovery factor" value={num(r.recovery_factor)} />
            <Stat label="Ulcer index" value={num(r.ulcer_index, 4)} />
            <Stat label="Tail ratio" value={num(r.tail_ratio)} />
          </div>
          <div className="banner info" style={{ marginTop: 12 }}>{r.proxy_note}</div>
        </>
      )}

      <div className="footer-note">
        Performance attribution only — <strong>not a tax record</strong>. FIFO matching serves
        performance analysis; there is no wash-sale handling, no tax-lot election, and no
        short/long-term split.
      </div>
    </>
  )
}
