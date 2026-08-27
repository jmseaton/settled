import { useCallback, useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  api,
  type AxisCatalog,
  type Chart,
  type DashboardLayout,
  type DashboardPanel,
  type Distribution,
} from '../api'
import { duration, money, num, pct } from '../format'

function formatValue(value: number | null, unit: string): string {
  if (value === null) return '—'
  if (unit === 'money') return money(value)
  if (unit === 'percent') return pct(value, 1)
  if (unit === 'seconds') return duration(value)
  if (unit === 'count') return num(value, 0)
  return num(value)
}

const axisStyle = {
  axisLine: { lineStyle: { color: '#2a2f3a' } },
  axisLabel: { color: '#98a0ae' },
}

function GridPanel({ panel, mode }: { panel: DashboardPanel; mode: string }) {
  const [chart, setChart] = useState<Chart | null>(null)

  useEffect(() => {
    if (!panel.dimension || !panel.metric) return
    const extra: Record<string, string> = {}
    if (panel.limit) extra.limit = String(panel.limit)
    if (mode) extra.mode = mode
    api.chart(panel.dimension, panel.metric, extra).then(setChart).catch(() => setChart(null))
  }, [panel.dimension, panel.metric, panel.limit, mode])

  if (!chart) return <p className="muted">Loading…</p>
  if (!chart.buckets.length) return <p className="muted">No units on this axis.</p>

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 62, right: 12, top: 14, bottom: chart.buckets.length > 6 ? 62 : 30 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const b = chart.buckets[params[0].dataIndex]
        return b
          ? `<strong>${b.label}</strong><br/>${chart.metric_label}: ${formatValue(
              b.value,
              chart.metric_unit,
            )}<br/>${b.unit_count} unit(s)${b.thin ? ' · thin' : ''}`
          : ''
      },
    },
    xAxis: {
      type: 'category',
      data: chart.buckets.map((b) => b.label),
      ...axisStyle,
      axisLabel: { color: '#98a0ae', rotate: chart.buckets.length > 6 ? 40 : 0, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#98a0ae',
        fontSize: 10,
        formatter: (v: number) => formatValue(v, chart.metric_unit),
      },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        type: 'bar',
        data: chart.buckets.map((b) => ({
          value: b.value,
          itemStyle: {
            opacity: b.thin ? 0.45 : 1,
            color: !chart.metric_signed
              ? '#58a6ff'
              : (b.value ?? 0) >= 0
                ? '#3fb950'
                : '#f85149',
          },
        })),
      },
    ],
  }
  return <ReactECharts option={option} style={{ height: 220 }} theme="dark" />
}

function DistributionPanel({ panel }: { panel: DashboardPanel }) {
  const [dist, setDist] = useState<Distribution | null>(null)
  useEffect(() => {
    api.distribution(panel.metric ?? 'net_pnl').then(setDist).catch(() => setDist(null))
  }, [panel.metric])

  if (!dist) return <p className="muted">Loading…</p>
  if (dist.insufficient) return <p className="muted">{dist.note}</p>

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 44, right: 12, top: 14, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const b = dist.bins[params[0].dataIndex]
        return b
          ? `${money(b.from)} to ${money(b.to)}<br/>${b.count} unit(s)`
          : ''
      },
    },
    xAxis: {
      type: 'category',
      data: dist.bins.map((b) => money(b.centre, 0)),
      ...axisStyle,
      axisLabel: { color: '#98a0ae', fontSize: 9, rotate: 40 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#98a0ae', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        type: 'bar',
        data: dist.bins.map((b) => ({
          value: b.count,
          itemStyle: { color: b.centre >= 0 ? '#3fb950' : '#f85149' },
        })),
        barCategoryGap: '8%',
      },
      {
        // §9.3 #7 — there to be departed from, not matched.
        type: 'line',
        data: dist.normal,
        showSymbol: false,
        smooth: true,
        lineStyle: { color: '#98a0ae', width: 1.5, type: 'dashed' },
      },
    ],
  }
  return (
    <>
      <ReactECharts option={option} style={{ height: 220 }} theme="dark" />
      <div className="muted" style={{ fontSize: 11 }}>
        {dist.count} units, normal overlay for comparison
      </div>
    </>
  )
}

function SpecialPanel({ panel }: { panel: DashboardPanel }) {
  const [series, setSeries] = useState<{ x: string[]; y: (number | null)[] } | null>(null)
  const [label, setLabel] = useState('')

  useEffect(() => {
    const key = panel.key === 'rolling_expectancy' ? 'trend' : panel.key
    if (key === 'trend') {
      api
        .trend(panel.metric ?? 'expectancy', panel.window ?? 20)
        .then((t) => {
          setSeries({ x: t.points.map((p) => String(p.sequence)), y: t.points.map((p) => p.sma) })
          setLabel(`${t.metric_label}, ${t.window}-trade SMA`)
        })
        .catch(() => setSeries(null))
      return
    }
    if (key === 'calendar') {
      api
        .calendar()
        .then((c) => {
          setSeries({ x: c.months.map((m) => m.month), y: c.months.map((m) => m.realized_net) })
          setLabel('Realized P&L by month')
        })
        .catch(() => setSeries(null))
      return
    }
    api
      .performance()
      .then((p) => {
        const x = p.series.map((s) => s.trading_day)
        if (key === 'equity_curve') {
          setSeries({ x, y: p.series.map((s) => s.account_value) })
          setLabel('Account value')
        } else if (key === 'drawdown') {
          setSeries({ x, y: p.series.map((s) => -s.drawdown) })
          setLabel('Drawdown')
        } else {
          setSeries({ x, y: p.series.map((s) => s.cumulative_net_pnl) })
          setLabel('Cumulative net P&L')
        }
      })
      .catch(() => setSeries(null))
  }, [panel.key, panel.metric, panel.window])

  if (!series) return <p className="muted">Loading…</p>
  if (!series.x.length) return <p className="muted">No data yet.</p>

  const isBar = panel.key === 'calendar'
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 62, right: 12, top: 14, bottom: 30 },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(v) },
    xAxis: { type: 'category', data: series.x, ...axisStyle, axisLabel: { color: '#98a0ae', fontSize: 10 } },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#98a0ae', fontSize: 10, formatter: (v: number) => `$${v.toLocaleString()}` },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      isBar
        ? {
            type: 'bar',
            data: series.y,
            itemStyle: {
              color: (p: { data: number | null }) => ((p.data ?? 0) >= 0 ? '#3fb950' : '#f85149'),
            },
          }
        : {
            type: 'line',
            data: series.y,
            showSymbol: false,
            lineStyle: { color: panel.key === 'drawdown' ? '#f85149' : '#58a6ff', width: 2 },
            areaStyle: {
              color:
                panel.key === 'drawdown' ? 'rgba(248,81,73,0.14)' : 'rgba(88,166,255,0.10)',
            },
          },
    ],
  }
  return (
    <>
      <ReactECharts option={option} style={{ height: 220 }} theme="dark" />
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
    </>
  )
}

/**
 * §9.3's dashboard, and §14.6.1's resolution of it.
 *
 * The spec admitted the default twelve were a guess and said to revisit
 * "once there is enough history to see which views you actually open". That
 * revisit cannot be done in advance by anyone — so instead of a second
 * guess, the set ships as an editable layout. Curation becomes something
 * you do as you notice what you keep opening.
 */
export function DashboardPage({ mode }: { mode: string }) {
  const [layout, setLayout] = useState<DashboardLayout | null>(null)
  const [axes, setAxes] = useState<AxisCatalog | null>(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [addDimension, setAddDimension] = useState('day_of_week')
  const [addMetric, setAddMetric] = useState('net_pnl')

  const load = useCallback(() => {
    api.dashboard().then(setLayout).catch((e) => setError(String(e)))
    api.chartAxes().then(setAxes).catch(() => undefined)
  }, [])

  useEffect(load, [load])

  async function save(panels: DashboardPanel[]) {
    setError(null)
    try {
      setLayout(await api.setDashboard(panels))
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    }
  }

  if (error && !layout) return <div className="banner err">{error}</div>
  if (!layout || !axes) return <p className="muted">Loading…</p>

  const panels = layout.panels

  function move(index: number, delta: number) {
    const next = [...panels]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    save(next)
  }

  return (
    <>
      <h1>Dashboard</h1>
      <p className="subtitle">
        §9.3's default set — <strong>a guess, and the spec says so</strong>. Edit it as you
        learn which views you actually open; that is what turns the guess into a preference
        (§14.6).
      </p>

      <div className="toolbar">
        <button className="primary" onClick={() => setEditing((e) => !e)}>
          {editing ? 'Done' : 'Edit layout'}
        </button>
        {layout.customized && (
          <button
            className="primary"
            onClick={() => api.resetDashboard().then(setLayout).catch((e) => setError(String(e)))}
          >
            Reset to default
          </button>
        )}
        <span className="muted">{layout.note}</span>
      </div>

      {error && <div className="banner err">{error}</div>}

      {editing && (
        <div className="panel">
          <div className="toolbar" style={{ marginBottom: 0 }}>
            <span className="muted">Add a chart</span>
            <select value={addDimension} onChange={(e) => setAddDimension(e.target.value)}>
              {axes.dimensions.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.label}
                </option>
              ))}
            </select>
            <select value={addMetric} onChange={(e) => setAddMetric(e.target.value)}>
              {axes.metrics.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
            <button
              className="primary"
              onClick={() => {
                const d = axes.dimensions.find((x) => x.key === addDimension)
                const m = axes.metrics.find((x) => x.key === addMetric)
                save([
                  ...panels,
                  {
                    key: `${addMetric}_${addDimension}_${Date.now()}`,
                    title: `${m?.label} by ${d?.label.toLowerCase()}`,
                    kind: 'chart',
                    dimension: addDimension,
                    metric: addMetric,
                    limit: null,
                    window: null,
                  },
                ])
              }}
            >
              Add
            </button>
          </div>
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
          gap: 14,
        }}
      >
        {panels.map((panel, i) => (
          <div className="panel" key={panel.key} style={{ margin: 0 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 6,
              }}
            >
              <strong style={{ fontSize: 13 }}>{panel.title}</strong>
              {editing && (
                <span style={{ display: 'flex', gap: 4 }}>
                  <button className="primary" style={{ padding: '2px 7px', fontSize: 11 }} onClick={() => move(i, -1)}>
                    ↑
                  </button>
                  <button className="primary" style={{ padding: '2px 7px', fontSize: 11 }} onClick={() => move(i, 1)}>
                    ↓
                  </button>
                  <button
                    className="primary"
                    style={{ padding: '2px 7px', fontSize: 11 }}
                    onClick={() => save(panels.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                </span>
              )}
            </div>
            {panel.kind === 'special' && panel.key === 'distribution' ? (
              <DistributionPanel panel={panel} />
            ) : panel.kind === 'special' ? (
              <SpecialPanel panel={panel} />
            ) : (
              <GridPanel panel={panel} mode={mode} />
            )}
          </div>
        ))}
      </div>

      <div className="footer-note">
        Every panel above is the same parameterized chart component (§9.2), so the dashboard
        is a saved selection over ~{axes.dimensions.length * axes.metrics.length} possible
        views rather than a set of bespoke widgets. Removing one costs nothing and adding one
        back is two dropdowns.
      </div>
    </>
  )
}
