import { useCallback, useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { api, type AxisCatalog, type RegimeMarker, type Trend } from '../api'
import { duration, money, num, pct } from '../format'

const input = {
  background: 'var(--panel-2)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '6px 9px',
  fontSize: 13,
}

function formatValue(value: number | null, unit: string): string {
  if (value === null) return '—'
  if (unit === 'money') return money(value)
  if (unit === 'percent') return pct(value, 1)
  if (unit === 'seconds') return duration(value)
  if (unit === 'count') return num(value, 0)
  return num(value)
}

/**
 * §9.4 trend analysis, and §6.6's regime markers on top of it.
 *
 * The x-axis is trade sequence, not date. That is the whole point: a
 * calendar axis spaces points by how much time passed, this one by how many
 * decisions were made, and a change in *behaviour* only shows up on the
 * second. §6.6 names this as the view that reveals a regime shift, which is
 * why the markers are drawn here first.
 */
export function TrendPage({ mode }: { mode: string }) {
  const [axes, setAxes] = useState<AxisCatalog | null>(null)
  const [metric, setMetric] = useState('net_pnl')
  const [window, setWindow] = useState(20)
  const [overlay, setOverlay] = useState<'sma' | 'ema' | 'both'>('both')
  const [data, setData] = useState<Trend | null>(null)
  const [markers, setMarkers] = useState<RegimeMarker[]>([])
  const [error, setError] = useState<string | null>(null)

  const [newDate, setNewDate] = useState('')
  const [newLabel, setNewLabel] = useState('')

  const load = useCallback(() => {
    api
      .trend(metric, window, mode ? { mode } : {})
      .then((t) => {
        setData(t)
        setMarkers(t.regime_markers)
        setError(null)
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)))
  }, [metric, window, mode])

  useEffect(() => {
    api.chartAxes().then(setAxes).catch(() => undefined)
  }, [])
  useEffect(load, [load])

  if (error && !data) return <div className="banner err">{error}</div>
  if (!data || !axes) return <p className="muted">Loading…</p>

  const points = data.points
  const unit = data.metric_unit

  // §6.6 — a marker is a date, the axis is a sequence, so each one is placed
  // at the first trade that closed on or after it. A marker after the last
  // trade has nowhere to sit and is listed rather than drawn.
  const markerLines = markers
    .map((m) => {
      const index = points.findIndex((p) => p.trading_day && p.trading_day >= m.occurred_on)
      return index === -1 ? null : { marker: m, index }
    })
    .filter((x): x is { marker: RegimeMarker; index: number } => x !== null)

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 74, right: 20, top: 34, bottom: 44 },
    legend: { textStyle: { color: '#98a0ae' }, top: 0 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const p = points[params[0].dataIndex]
        if (!p) return ''
        return [
          `<strong>#${p.sequence} · ${p.label}</strong>`,
          p.trading_day ?? '',
          `${data.metric_label}: ${formatValue(p.value, unit)}`,
          p.sma !== null ? `SMA(${data.window}): ${formatValue(p.sma, unit)}` : '',
          p.ema !== null ? `EMA(${data.window}): ${formatValue(p.ema, unit)}` : '',
        ]
          .filter(Boolean)
          .join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: points.map((p) => p.sequence),
      name: 'trade #',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: '#98a0ae' },
      axisLine: { lineStyle: { color: '#2a2f3a' } },
      axisLabel: { color: '#98a0ae' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#98a0ae', formatter: (v: number) => formatValue(v, unit) },
      splitLine: { lineStyle: { color: '#1e222b' } },
    },
    series: [
      {
        name: data.metric_label,
        type: 'bar',
        data: points.map((p) => p.value),
        itemStyle: {
          color: (p: { data: number | null }) =>
            (p.data ?? 0) >= 0 ? 'rgba(63,185,80,0.55)' : 'rgba(248,81,73,0.55)',
        },
        // §6.6 — the markers hang off the first series so ECharts draws them
        // as vertical lines across the plot.
        markLine:
          markerLines.length > 0
            ? {
                symbol: 'none',
                silent: true,
                label: {
                  color: '#d29922',
                  formatter: (p: { name: string }) => p.name,
                  rotate: 90,
                  position: 'insideEndTop',
                  fontSize: 11,
                },
                lineStyle: { color: '#d29922', type: 'dashed', width: 1.5 },
                data: markerLines.map(({ marker, index }) => ({
                  xAxis: index,
                  name: marker.label,
                })),
              }
            : undefined,
      },
      ...(overlay !== 'ema'
        ? [
            {
              name: `SMA(${data.window})`,
              type: 'line',
              data: points.map((p) => p.sma),
              showSymbol: false,
              connectNulls: false,
              lineStyle: { color: '#58a6ff', width: 2 },
            },
          ]
        : []),
      ...(overlay !== 'sma'
        ? [
            {
              name: `EMA(${data.window})`,
              type: 'line',
              data: points.map((p) => p.ema),
              showSymbol: false,
              lineStyle: { color: '#d29922', width: 1.5, type: 'dotted' },
            },
          ]
        : []),
    ],
  }

  return (
    <>
      <h1>Trend</h1>
      <p className="subtitle">
        Any metric per <strong>trade</strong> rather than per date, with a moving average
        (§9.4). Answers "is my edge improving over the last {data.window}?" — and it is the
        view a change in how you trade actually shows up on.
      </p>

      <div className="toolbar">
        <label className="muted">
          Metric{' '}
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {axes.metrics.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="muted">
          Window{' '}
          <select value={window} onChange={(e) => setWindow(Number(e.target.value))}>
            {[5, 10, 20, 50].map((w) => (
              <option key={w} value={w}>
                {w} trades
              </option>
            ))}
          </select>
        </label>
        <label className="muted">
          Overlay{' '}
          <select value={overlay} onChange={(e) => setOverlay(e.target.value as typeof overlay)}>
            <option value="both">SMA and EMA</option>
            <option value="sma">SMA only</option>
            <option value="ema">EMA only</option>
          </select>
        </label>
        <span className="muted">
          {data.unit_count} unit(s), analyzing by{' '}
          {data.analysis_mode === 'STRATEGY' ? 'strategy' : 'leg'}
          {data.undefined_units > 0 &&
            ` · ${data.undefined_units} have no value on this metric and are skipped rather than counted as zero`}
        </span>
      </div>

      {error && <div className="banner err">{error}</div>}

      <div className="panel">
        {points.length ? (
          <ReactECharts option={option} style={{ height: 380 }} theme="dark" />
        ) : (
          <p className="muted">No closed units yet.</p>
        )}
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          {data.axis_note} {data.window_note}
        </div>
      </div>

      <h2>Regime markers</h2>
      <div className="panel">
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
          The app deliberately does not detect regime changes — §6.6 calls that
          over-engineering for one user. Write down when you changed how you trade, and it
          becomes a line on this chart. The fixture's own case: MES was verticals through
          2026-07-05 and naked short puts from 2026-07-12, and a single "MES win rate" spans
          both.
        </p>
        <div className="toolbar">
          <input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} />
          <input
            type="text"
            placeholder="What changed, e.g. MES: verticals → naked short puts"
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            style={{ ...input, minWidth: 340 }}
          />
          <button
            className="primary"
            disabled={!newDate || !newLabel.trim()}
            onClick={() =>
              api
                .createRegimeMarker({ occurred_on: newDate, label: newLabel })
                .then(() => {
                  setNewDate('')
                  setNewLabel('')
                  load()
                })
                .catch((e) => setError(String(e instanceof Error ? e.message : e)))
            }
          >
            Add marker
          </button>
        </div>

        {markers.length ? (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Label</th>
                <th>On chart</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {markers.map((m) => {
                const placed = markerLines.some((l) => l.marker.id === m.id)
                return (
                  <tr key={m.id}>
                    <td>{m.occurred_on}</td>
                    <td>{m.label}</td>
                    <td className="muted">
                      {placed ? 'yes' : 'after the last trade — nothing to mark yet'}
                    </td>
                    <td>
                      <button
                        className="primary"
                        style={{ padding: '3px 9px', fontSize: 12 }}
                        onClick={() => api.deleteRegimeMarker(m.id).then(load)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <p className="muted">None yet.</p>
        )}
      </div>
    </>
  )
}
