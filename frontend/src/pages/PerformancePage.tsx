import { useCallback, useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { api, type Performance } from '../api'
import { money, num, pct, signClass } from '../format'
import { EpochPanel } from './EpochPanel'

const VARIANTS = [
  { key: 'WITH_FLOWS', label: 'P&L + deposits/withdrawals', hint: '§7.2 — the account equity curve' },
  { key: 'PNL_ONLY', label: 'P&L only', hint: 'Cumulative realized P&L, no cash flows' },
  { key: 'WITH_INCOME', label: '+ dividends, interest & fees', hint: 'Closer to the real balance' },
] as const

function Stat({ label, value, cls, note }: { label: string; value: string; cls?: string; note?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value ${cls ?? ''}`}>{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  )
}

export function PerformancePage() {
  const [variant, setVariant] = useState<string>('WITH_FLOWS')
  const [showBenchmark, setShowBenchmark] = useState(true)
  const [data, setData] = useState<Performance | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.performance(variant).then(setData).catch((e) => setError(String(e)))
  }, [variant])

  useEffect(load, [load])

  if (error) return <div className="banner err">{error}</div>
  if (!data) return <p className="muted">Loading…</p>

  const { series, returns, benchmark, epoch, ratios } = data
  const days = series.map((s) => s.trading_day)
  const hasBenchmark = series.some((s) => s.benchmark_shadow_value !== null)

  const axis = {
    axisLine: { lineStyle: { color: '#2a2f3a' } },
    axisLabel: { color: '#98a0ae' },
  }

  const curveOption = {
    backgroundColor: 'transparent',
    grid: { left: 66, right: 20, top: 30, bottom: 40 },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(v) },
    legend: { textStyle: { color: '#98a0ae' }, top: 0 },
    xAxis: { type: 'category', data: days, ...axis },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#98a0ae', formatter: (v: number) => `$${v.toLocaleString()}` },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        name: 'Account',
        type: 'line',
        data: series.map((s) => s.value),
        showSymbol: false,
        lineStyle: { color: '#58a6ff', width: 2 },
        areaStyle: { color: 'rgba(88,166,255,0.10)' },
      },
      ...(showBenchmark && hasBenchmark
        ? [
            {
              name: `${benchmark.symbol} (cash-flow matched)`,
              type: 'line',
              data: series.map((s) => s.benchmark_shadow_value),
              showSymbol: false,
              // Dashed, because it is a shadow portfolio rather than a
              // holding — a solid second line reads as a second account.
              lineStyle: { color: '#d29922', width: 1.5, type: 'dashed' },
            },
          ]
        : []),
    ],
  }

  const drawdownOption = {
    backgroundColor: 'transparent',
    grid: { left: 66, right: 20, top: 16, bottom: 34 },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(-Math.abs(v)) },
    xAxis: { type: 'category', data: days, ...axis },
    yAxis: {
      type: 'value',
      inverse: true,
      axisLabel: { color: '#98a0ae', formatter: (v: number) => `-$${v.toLocaleString()}` },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        type: 'line',
        data: series.map((s) => s.drawdown),
        showSymbol: false,
        lineStyle: { color: '#f85149', width: 1.5 },
        areaStyle: { color: 'rgba(248,81,73,0.14)' },
      },
    ],
  }

  const gated = benchmark.insufficient_sample

  return (
    <>
      <h1>Performance</h1>
      <p className="subtitle">
        Account-level returns measured from the performance epoch (§0.3, §7). Trade statistics
        live on the Statistics page; this page is about the account, deposits included.
      </p>

      <EpochPanel epoch={epoch} onChange={load} />

      {!epoch.configured && <div className="banner warn">{epoch.note}</div>}

      <div className="toolbar">
        <select value={variant} onChange={(e) => setVariant(e.target.value)}>
          {VARIANTS.map((v) => (
            <option key={v.key} value={v.key}>
              {v.label}
            </option>
          ))}
        </select>
        <span className="muted">{VARIANTS.find((v) => v.key === variant)?.hint}</span>
        <label className="muted" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={showBenchmark}
            onChange={(e) => setShowBenchmark(e.target.checked)}
          />
          {benchmark.symbol} overlay
        </label>
      </div>

      <h2>Equity curve</h2>
      <div className="panel">
        {series.length > 0 ? (
          <ReactECharts option={curveOption} style={{ height: 320 }} theme="dark" />
        ) : (
          <p className="muted">Nothing imported yet.</p>
        )}
        {returns.excludes_unrealized && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            {returns.unrealized_note}
          </div>
        )}
      </div>

      <h2>Returns</h2>
      {returns.available ? (
        <>
          <div className="stat-grid">
            <Stat
              label="TWR (headline)"
              value={pct(returns.twr, 2)}
              cls={signClass(returns.twr)}
              note="Trading skill — contributions stripped out"
            />
            <Stat
              label="XIRR"
              value={pct(returns.xirr, 2)}
              cls={signClass(returns.xirr)}
              note="Return on your actual dollars"
            />
            <Stat label="CAGR" value={pct(returns.cagr, 2)} cls={signClass(returns.cagr)} note="Annualized from TWR" />
            <Stat
              label="Simple return"
              value={pct(returns.simple_return, 2)}
              cls={signClass(returns.simple_return)}
              note="The naive measure"
            />
            <Stat label="Starting value" value={money(returns.starting_value)} note="At the epoch" />
            <Stat label="Ending value" value={money(returns.ending_value)} />
            <Stat label="Net deposits" value={money(returns.net_deposits)} />
            <Stat
              label="Unrealized (open book)"
              value={money(returns.closing_unrealized)}
              cls={signClass(returns.closing_unrealized)}
              note={`Marked on ${returns.marked_days ?? 0} of ${series.length} days`}
            />
            <Stat label="Period" value={`${returns.span_days} days`} />
          </div>
          <div className="banner info" style={{ marginTop: 12 }}>
            <strong>TWR is the headline.</strong> {returns.twr_note} {returns.xirr_note} The gap
            between the two is itself informative.
            {returns.basis_note ? ` ${returns.basis_note}` : ''}
          </div>
        </>
      ) : (
        <div className="banner warn">{returns.reason}</div>
      )}

      <h2>Risk-adjusted, on account returns</h2>
      {ratios.insufficient_sample ? (
        <div className="banner warn">
          <strong>Insufficient data.</strong> These need at least{' '}
          {ratios.min_sample_trading_days} trading days of account returns; this has{' '}
          {ratios.sample_trading_days}.
        </div>
      ) : (
        <>
          <div className="stat-grid">
            <Stat
              label="Sharpe"
              value={
                ratios.sharpe_significance?.standard_error
                  ? `${num(ratios.sharpe)} ± ${num(ratios.sharpe_significance.standard_error)}`
                  : num(ratios.sharpe)
              }
              note={
                ratios.sharpe_significance?.confidence_95
                  ? `95% [${num(ratios.sharpe_significance.confidence_95[0])}, ${num(
                      ratios.sharpe_significance.confidence_95[1],
                    )}]`
                  : undefined
              }
            />
            <Stat label="Sortino" value={num(ratios.sortino)} />
            <Stat label="Calmar" value={num(ratios.calmar)} />
            <Stat label="Omega" value={num(ratios.omega)} />
            <Stat label="Ulcer index" value={num(ratios.ulcer_index, 4)} />
            <Stat label="Max drawdown" value={pct(ratios.max_drawdown_pct, 2)} cls="neg" />
            <Stat label="Annualized return" value={pct(ratios.annualized_return, 2)} cls={signClass(ratios.annualized_return)} />
            <Stat label="Annualized volatility" value={pct(ratios.annualized_volatility, 2)} />
          </div>
          {/* §14.6.3 — the interval, not the sample-size cutoff, is what
              says how much of this Sharpe the data supports. Drawn as a
              warning when it straddles zero, because that is the case a
              bare number would most mislead about. */}
          <div
            className={`banner ${
              ratios.sharpe_significance?.significant_at_95 === false ? 'warn' : 'info'
            }`}
            style={{ marginTop: 12 }}
          >
            <strong>Sharpe, with its uncertainty.</strong>{' '}
            {ratios.sharpe_significance?.note} {ratios.sharpe_significance?.method}
          </div>
          <div className="banner info" style={{ marginTop: 12 }}>{ratios.note}</div>
        </>
      )}

      <h2>Benchmark — {benchmark.symbol}</h2>
      <div className="panel">
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>{benchmark.method}</div>
        {benchmark.coverage_note && <div className="banner warn">{benchmark.coverage_note}</div>}
        {benchmark.is_stale && benchmark.priced_days > 0 && (
          <div className="banner warn">
            Benchmark data is stale — {benchmark.stale_days} day(s) are carried forward from an
            older close. Last real bar: {benchmark.latest_priced_day ?? 'none'}.
          </div>
        )}
        {gated ? (
          <div className="banner warn">
            <strong>Insufficient data.</strong> Alpha, beta, correlation, tracking error and the
            information ratio need at least {benchmark.min_sample_trading_days} trading days.
            This has {benchmark.sample_trading_days}. A beta computed from less would be noise
            with a decimal point.
          </div>
        ) : (
          <div className="stat-grid">
            <Stat
              label="Gap vs. doing nothing"
              value={money(benchmark.value_gap)}
              cls={signClass(benchmark.value_gap)}
              note={`Account ${money(benchmark.account_final)} vs shadow ${money(benchmark.benchmark_final)}`}
            />
            <Stat
              label="Account (time-weighted)"
              value={pct(benchmark.account_return, 2)}
              cls={signClass(benchmark.account_return)}
              note="Flows netted out"
            />
            <Stat
              label={`${benchmark.symbol} (time-weighted)`}
              value={pct(benchmark.benchmark_return, 2)}
              cls={signClass(benchmark.benchmark_return)}
            />
            <Stat label="Excess" value={pct(benchmark.excess_return, 2)} cls={signClass(benchmark.excess_return)} />
            <Stat label="Alpha (annualized)" value={pct(benchmark.alpha_annualized, 2)} cls={signClass(benchmark.alpha_annualized)} />
            <Stat label="Beta" value={num(benchmark.beta)} />
            <Stat label="Correlation" value={num(benchmark.correlation)} />
            <Stat label="Tracking error (ann.)" value={pct(benchmark.tracking_error_annualized, 2)} />
            <Stat label="Information ratio" value={num(benchmark.information_ratio)} />
          </div>
        )}
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>{benchmark.note}</div>
        <button
          className="primary"
          style={{ marginTop: 12 }}
          onClick={() => api.refreshBenchmark().then(load).catch((e) => setError(String(e)))}
        >
          Refresh prices
        </button>
      </div>

      <h2>Drawdown</h2>
      <div className="panel">
        {series.length > 0 ? (
          <ReactECharts option={drawdownOption} style={{ height: 200 }} theme="dark" />
        ) : (
          <p className="muted">No data.</p>
        )}
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          Underwater plot against the account's own running peak. Realized-only, so a position
          held through a decline shows no drawdown until it is closed.
        </div>
      </div>
    </>
  )
}
