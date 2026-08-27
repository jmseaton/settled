import { useCallback, useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  api,
  filterParams,
  type AxisCatalog,
  type Chart,
  type FilterOptions,
  type GlobalFilters,
} from '../api'
import { duration, money, num, pct } from '../format'

/** §9.3's curated default set. The grid can draw any pair; these are the
 *  ones worth opening with, and every one of them is the same component. */
const PRESETS: { label: string; dimension: string; metric: string; limit?: number }[] = [
  { label: 'P&L by day of week', dimension: 'day_of_week', metric: 'net_pnl' },
  { label: 'P&L by time of day', dimension: 'time_of_day', metric: 'net_pnl' },
  { label: 'Win rate by strategy', dimension: 'strategy_type', metric: 'win_rate' },
  { label: 'P&L by underlying', dimension: 'underlying', metric: 'net_pnl', limit: 10 },
  { label: 'Commissions by month', dimension: 'month', metric: 'commissions' },
  { label: 'Return on risk by DTE', dimension: 'dte_at_open', metric: 'return_on_risk' },
  { label: 'Expectancy by hold time', dimension: 'hold_time', metric: 'expectancy' },
  { label: 'Trade count by month', dimension: 'month', metric: 'trade_count' },
]

function formatValue(value: number | null, unit: string): string {
  if (value === null) return '—'
  if (unit === 'money') return money(value)
  if (unit === 'percent') return pct(value, 1)
  if (unit === 'seconds') return duration(value)
  if (unit === 'count') return num(value, 0)
  return num(value)
}

function MultiSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  if (!options.length) return null
  return (
    <label className="muted">
      {label}
      <select
        multiple
        size={Math.min(options.length, 4)}
        value={selected}
        onChange={(e) =>
          onChange(Array.from(e.target.selectedOptions).map((o) => o.value))
        }
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  )
}

export function ChartsPage({ mode }: { mode: string }) {
  const [axes, setAxes] = useState<AxisCatalog | null>(null)
  const [options, setOptions] = useState<FilterOptions | null>(null)
  const [dimension, setDimension] = useState('day_of_week')
  const [metric, setMetric] = useState('net_pnl')
  const [limit, setLimit] = useState<number | undefined>(undefined)
  const [filters, setFilters] = useState<GlobalFilters>({})
  const [chart, setChart] = useState<Chart | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.chartAxes().then(setAxes).catch((e) => setError(String(e)))
    api.chartFilterOptions().then(setOptions).catch(() => undefined)
  }, [])

  const load = useCallback(() => {
    const extra = filterParams(filters)
    if (limit) extra.limit = String(limit)
    if (mode) extra.mode = mode
    api
      .chart(dimension, metric, extra)
      .then((c) => {
        setChart(c)
        setError(null)
      })
      .catch((e) => setError(String(e)))
  }, [dimension, metric, limit, filters, mode])

  useEffect(load, [load])

  if (error && !chart) return <div className="banner err">{error}</div>
  if (!axes) return <p className="muted">Loading…</p>

  const spec = axes.metrics.find((m) => m.key === metric)
  const unit = spec?.unit ?? 'ratio'
  const buckets = chart?.buckets ?? []

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 76, right: 20, top: 20, bottom: 76 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const b = buckets[params[0].dataIndex]
        if (!b) return ''
        return [
          `<strong>${b.label}</strong>`,
          `${chart?.metric_label}: ${formatValue(b.value, unit)}`,
          `${b.unit_count} unit(s) · net ${money(b.net_pnl)}`,
          b.thin ? `<em>thin bucket — under ${chart?.thin_bucket_threshold} units</em>` : '',
        ]
          .filter(Boolean)
          .join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: buckets.map((b) => b.label),
      axisLine: { lineStyle: { color: '#2a2f3a' } },
      axisLabel: { color: '#98a0ae', rotate: buckets.length > 8 ? 40 : 0 },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#98a0ae',
        formatter: (v: number) => formatValue(v, unit),
      },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        type: 'bar',
        data: buckets.map((b) => ({
          value: b.value,
          itemStyle: {
            // Thin buckets are drawn translucent: a 100% win rate off one
            // trade should not look like a solid finding.
            opacity: b.thin ? 0.45 : 1,
            color: !spec?.signed
              ? '#58a6ff'
              : (b.value ?? 0) >= 0
                ? '#3fb950'
                : '#f85149',
          },
        })),
      },
    ],
  }

  const activeFilters = Object.entries(filters).filter(
    ([, v]) => v && (Array.isArray(v) ? v.length : true),
  )

  return (
    <>
      <h1>Charts</h1>
      <p className="subtitle">
        Any metric against any dimension (§9.1–9.2) — {axes.dimensions.length} ×{' '}
        {axes.metrics.length} combinations from one component. Click a bar to filter everything
        down to that bucket.
      </p>

      <div className="toolbar">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            className="primary"
            style={{
              fontSize: 12,
              padding: '5px 10px',
              opacity: p.dimension === dimension && p.metric === metric ? 1 : 0.55,
            }}
            onClick={() => {
              setDimension(p.dimension)
              setMetric(p.metric)
              setLimit(p.limit)
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="toolbar">
        <label className="muted">
          X{' '}
          <select value={dimension} onChange={(e) => setDimension(e.target.value)}>
            {axes.dimensions.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label className="muted">
          Y{' '}
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {axes.metrics.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="muted">
          Top/bottom{' '}
          <select
            value={limit ?? ''}
            onChange={(e) => setLimit(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">all buckets</option>
            <option value="5">5</option>
            <option value="10">10</option>
          </select>
        </label>
      </div>

      <div className="toolbar filter-bar">
        <label className="muted">
          From{' '}
          <input
            type="date"
            value={filters.date_from ?? ''}
            onChange={(e) => setFilters({ ...filters, date_from: e.target.value || undefined })}
          />
        </label>
        <label className="muted">
          To{' '}
          <input
            type="date"
            value={filters.date_to ?? ''}
            onChange={(e) => setFilters({ ...filters, date_to: e.target.value || undefined })}
          />
        </label>
        <MultiSelect
          label="Book"
          options={options?.books ?? []}
          selected={filters.books ?? []}
          onChange={(books) => setFilters({ ...filters, books })}
        />
        <MultiSelect
          label="Underlying"
          options={options?.underlyings ?? []}
          selected={filters.underlyings ?? []}
          onChange={(underlyings) => setFilters({ ...filters, underlyings })}
        />
        <MultiSelect
          label="Asset class"
          options={options?.asset_classes ?? []}
          selected={filters.asset_classes ?? []}
          onChange={(asset_classes) => setFilters({ ...filters, asset_classes })}
        />
        {activeFilters.length > 0 && (
          <button className="primary" onClick={() => setFilters({})}>
            Clear filters
          </button>
        )}
      </div>

      {error && <div className="banner err">{error}</div>}

      <div className="panel">
        {buckets.length ? (
          <ReactECharts
            option={option}
            style={{ height: 340 }}
            theme="dark"
            onEvents={{
              // §9 — "click a data point to drill into the underlying
              // trades; right-click to apply that bucket as a global
              // filter". One generic interaction, every chart.
              click: (params: { dataIndex: number }) => {
                const bucket = buckets[params.dataIndex]
                if (!bucket) return
                if (dimension === 'underlying') {
                  setFilters({ ...filters, underlyings: [bucket.key] })
                } else if (dimension === 'book') {
                  setFilters({ ...filters, books: [bucket.key] })
                } else if (dimension === 'asset_class') {
                  setFilters({ ...filters, asset_classes: [bucket.key] })
                }
              },
            }}
          />
        ) : (
          <p className="muted">No units fall on this axis.</p>
        )}
      </div>

      {chart && (
        <>
          <div className="muted" style={{ fontSize: 12, marginBottom: 14 }}>
            {chart.unit_count} unit(s) in {chart.bucket_count} bucket(s), analyzing by{' '}
            {chart.analysis_mode === 'STRATEGY' ? 'strategy' : 'leg'}.
            {chart.truncated_buckets > 0 &&
              ` ${chart.truncated_buckets} bucket(s) hidden by the top/bottom limit — still counted in the totals.`}
            {chart.unplaced_note && ` ${chart.unplaced_note}`}
          </div>

          <div className="scroll" style={{ maxHeight: '40vh' }}>
            <table>
              <thead>
                <tr>
                  <th>{chart.dimension_label}</th>
                  <th className="num">{chart.metric_label}</th>
                  <th className="num">Units</th>
                  {/* Net P&L is shown alongside every metric for context —
                      except when it *is* the metric, where a second
                      identical column is just noise. */}
                  {metric !== 'net_pnl' && <th className="num">Net P&amp;L</th>}
                </tr>
              </thead>
              <tbody>
                {buckets.map((b) => (
                  <tr key={b.key} style={{ opacity: b.thin ? 0.6 : 1 }}>
                    <td>
                      {b.label} {b.thin && <span className="tag">thin</span>}
                    </td>
                    <td className="num">{formatValue(b.value, unit)}</td>
                    <td className="num">{b.unit_count}</td>
                    {metric !== 'net_pnl' && <td className="num">{money(b.net_pnl)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="footer-note">
        Buckets holding fewer than {chart?.thin_bucket_threshold ?? 5} units are drawn
        translucent and tagged: a 100% win rate off one trade is one trade, not an edge.
        {axes.unavailable.length > 0 && (
          <>
            {' '}
            Not yet available as dimensions:{' '}
            {axes.unavailable.map((u) => `${u.key} (${u.reason})`).join(', ')}.
          </>
        )}
      </div>
    </>
  )
}
