import { useEffect, useState } from 'react'
import { api, type ExpiryRow } from '../api'
import { money, num } from '../format'

export function ExpiryWatchPage() {
  const [asOf, setAsOf] = useState('2026-08-07')
  const [rows, setRows] = useState<ExpiryRow[]>([])
  const [disclaimer, setDisclaimer] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .expiryWatch(asOf)
      .then((r) => { setRows(r.items); setDisclaimer(r.disclaimer) })
      .catch((e) => setError(String(e)))
  }, [asOf])

  if (error) return <div className="banner err">{error}</div>

  const soon = rows.filter((r) => r.dte_calendar !== null && r.dte_calendar <= 5)

  return (
    <>
      <h1>Expiry Watch</h1>
      <p className="subtitle">
        The one forward-looking view: what each open option position becomes if it finishes in the
        money.
      </p>

      <div className="toolbar">
        <label className="muted">As of</label>
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        <span className="muted">{rows.length} open option position(s)</span>
      </div>

      <div className="banner info">{disclaimer}</div>

      {soon.length > 0 && (
        <div className="banner warn">
          <strong>{soon.length} position(s) expire within 5 days.</strong> European-style contracts
          auto-exercise if in the money, with no abandonment allowed.
        </div>
      )}

      {rows.length === 0 ? (
        <p className="muted">No open option positions as of this date.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Underlying</th>
                <th className="num">Qty</th>
                <th className="num">Strike</th>
                <th>Right</th>
                <th>Expiry</th>
                <th className="num">DTE (cal)</th>
                <th className="num">DTE (trading)</th>
                <th>Exercise style</th>
                <th>If assigned / exercised</th>
                <th className="num">Notional</th>
                <th className="num">Cash required</th>
                <th className="num">Underlying</th>
                <th className="num">To strike</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.instrument_id}>
                  <td>{r.symbol.trim()}</td>
                  <td>{r.underlying ?? '—'}</td>
                  <td className={`num ${r.quantity < 0 ? 'neg' : 'pos'}`}>{num(r.quantity, 0)}</td>
                  <td className="num">{num(r.strike)}</td>
                  <td>{r.right ?? '—'}</td>
                  <td>{r.expiry ?? '—'}</td>
                  <td className="num">{r.dte_calendar ?? '—'}</td>
                  <td className="num">{r.dte_trading ?? '—'}</td>
                  <td>
                    <span className="tag">{r.exercise_style}</span>
                  </td>
                  <td>{r.projection}</td>
                  <td className="num">{money(r.projection_notional)}</td>
                  <td className="num">{money(r.projection_cash_required)}</td>
                  <td className="num muted" title={r.reference_price_source ?? ''}>
                    {r.reference_price === null ? 'no price' : num(r.reference_price)}
                  </td>
                  <td className={`num ${r.moneyness === 'ITM' ? 'neg' : ''}`}>
                    {/* §5.4 — a forecast, never an outcome. ITM settles on a
                        price fixing this app cannot observe, so the label is
                        "where the last mark sits", not "what will happen". */}
                    {r.distance_to_strike === null
                      ? '—'
                      : `${num(r.distance_to_strike)} (${r.moneyness})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.some((r) => r.warnings.length > 0) && (
        <>
          <h2>Row notes</h2>
          <div className="panel">
            {rows.flatMap((r) =>
              r.warnings.map((w, i) => (
                <div key={`${r.instrument_id}-${i}`} style={{ padding: '5px 0', fontSize: 13, lineHeight: 1.5 }}>
                  <strong>{r.symbol.trim()}</strong> — {w}
                </div>
              )),
            )}
          </div>
        </>
      )}

      <div className="footer-note">
        Distance to strike is shown wherever the underlying has a price — the broker's own mark if
        the underlying is held, otherwise a cached daily close (§4.10). It is a{' '}
        <strong>forecast</strong>: in-the-money status settles on a 3 p.m. CT price fixing this app
        cannot observe, so a position can read comfortably out of the money here and still
        exercise. Where no price is cached the column is blank rather than guessed; expiry,
        strike, quantity and the resulting-position projection are the operationally important
        fields and none of them need a price at all.
      </div>
    </>
  )
}
