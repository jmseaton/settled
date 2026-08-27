import { useEffect, useState } from 'react'
import { api, type CalendarBody, type CalendarDay } from '../api'
import { money, signClass } from '../format'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** Colour intensity scaled against the best and worst *active* day.
 *  Scaling against every day would include the flat ones and compress the
 *  whole range toward the middle, making a good month look like a quiet
 *  one. A flat day gets the panel colour, not a pale green. */
function cellColor(net: number, maxGain: number, maxLoss: number): string {
  if (!net) return 'var(--panel-2)'
  const share = net > 0 ? net / (maxGain || 1) : net / (maxLoss || -1)
  const alpha = 0.15 + 0.6 * Math.min(Math.abs(share), 1)
  return net > 0 ? `rgba(63,185,80,${alpha})` : `rgba(248,81,73,${alpha})`
}

function monthGrid(days: CalendarDay[]): (CalendarDay | null)[] {
  if (!days.length) return []
  const first = new Date(`${days[0].trading_day}T00:00:00`)
  // Monday-first, matching the weekday header.
  const offset = (first.getDay() + 6) % 7
  const byDate = new Map(days.map((d) => [d.trading_day, d]))

  const cells: (CalendarDay | null)[] = Array(offset).fill(null)
  const last = new Date(`${days[days.length - 1].trading_day}T00:00:00`)
  for (let cursor = new Date(first); cursor <= last; cursor.setDate(cursor.getDate() + 1)) {
    cells.push(byDate.get(cursor.toISOString().slice(0, 10)) ?? null)
  }
  return cells
}

export function CalendarPage() {
  const [data, setData] = useState<CalendarBody | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<CalendarDay | null>(null)

  useEffect(() => {
    api.calendar().then(setData).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="banner err">{error}</div>
  if (!data) return <p className="muted">Loading…</p>
  if (!data.days.length) return <p className="muted">Nothing imported yet.</p>

  const { max_gain: maxGain, max_loss: maxLoss } = data.scale
  const byMonth = new Map<string, CalendarDay[]>()
  for (const day of data.days) {
    const key = day.trading_day.slice(0, 7)
    byMonth.set(key, [...(byMonth.get(key) ?? []), day])
  }

  return (
    <>
      <h1>Calendar</h1>
      <p className="subtitle">
        Realized P&amp;L per trading day (§9.3 #3). Keyed on the trading day, not the calendar
        date, so an evening futures fill sits on the session it belongs to. Click a day for its
        detail.
      </p>

      <div className="stat-grid">
        <div className="stat">
          <div className="label">Best day</div>
          <div className="value pos">{money(maxGain)}</div>
        </div>
        <div className="stat">
          <div className="label">Worst day</div>
          <div className="value neg">{money(maxLoss)}</div>
        </div>
        <div className="stat">
          <div className="label">Days traded</div>
          <div className="value">{data.scale.active_days}</div>
          <div className="note">{data.scale.flat_days} flat</div>
        </div>
      </div>

      {selected && (
        <div className="banner info" style={{ marginTop: 14 }}>
          <strong>{selected.trading_day}</strong> · realized {money(selected.realized_net)} ·{' '}
          {selected.trade_count} trade(s), {selected.win_count}W/{selected.loss_count}L ·
          commissions {money(selected.commissions)}
          {selected.cash_flow !== 0 && ` · cash flow ${money(selected.cash_flow)}`} · account
          value {money(selected.account_value)}
        </div>
      )}

      {[...byMonth.entries()].map(([month, days]) => {
        const summary = data.months.find((m) => m.month === month)
        return (
          <div key={month}>
            <h2>
              {month}{' '}
              <span className={signClass(summary?.realized_net ?? 0)}>
                {money(summary?.realized_net ?? 0)}
              </span>{' '}
              <span className="muted" style={{ fontWeight: 400 }}>
                · {summary?.winning_days ?? 0} up, {summary?.losing_days ?? 0} down
              </span>
            </h2>
            <div className="panel">
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(7, 1fr)',
                  gap: 4,
                  maxWidth: 620,
                }}
              >
                {WEEKDAYS.map((w) => (
                  <div
                    key={w}
                    className="muted"
                    style={{ fontSize: 11, textAlign: 'center', paddingBottom: 4 }}
                  >
                    {w}
                  </div>
                ))}
                {monthGrid(days).map((day, i) =>
                  day === null ? (
                    <div key={i} />
                  ) : (
                    <button
                      key={day.trading_day}
                      onClick={() => setSelected(day)}
                      title={`${day.trading_day}: ${money(day.realized_net)} over ${day.trade_count} trade(s)`}
                      style={{
                        background: cellColor(day.realized_net, maxGain, maxLoss),
                        border: `1px solid ${
                          selected?.trading_day === day.trading_day
                            ? 'var(--accent)'
                            : 'var(--border)'
                        }`,
                        borderRadius: 6,
                        padding: '8px 4px',
                        color: 'var(--text)',
                        cursor: 'pointer',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                        {day.trading_day.slice(8)}
                      </div>
                      <div style={{ fontSize: 11 }}>
                        {day.realized_net ? money(day.realized_net, 0) : '·'}
                      </div>
                    </button>
                  ),
                )}
              </div>
            </div>
          </div>
        )
      })}

      <div className="footer-note">
        Days the market was open with no activity are shown flat rather than omitted — an empty
        cell and a zero cell mean different things, and this is where that difference is most
        visible.
      </div>
    </>
  )
}
