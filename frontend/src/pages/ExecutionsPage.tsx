import { useEffect, useState } from 'react'
import { api, type Execution } from '../api'
import { dateTime, money, num, signClass } from '../format'

export function ExecutionsPage() {
  const [rows, setRows] = useState<Execution[]>([])
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')

  useEffect(() => {
    api.executions().then((r) => setRows(r.items)).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="banner err">{error}</div>

  const filtered = rows.filter((r) => !q || r.symbol.toLowerCase().includes(q.toLowerCase()))

  return (
    <>
      <h1>Executions</h1>
      <p className="subtitle">
        Raw broker fills, exactly as imported. Never mutated — every trade and statistic is
        recomputed from these rows.
      </p>

      <div className="toolbar">
        <input
          type="text"
          placeholder="Filter by symbol…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{
            background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '6px 9px', fontSize: 13,
          }}
        />
        <span className="muted">{filtered.length} of {rows.length} executions</span>
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Order ID</th>
              <th>Symbol</th>
              <th>Class</th>
              <th>Action</th>
              <th>Flags</th>
              <th>Executed at (ET)</th>
              <th>Trading day</th>
              <th className="num">Qty</th>
              <th className="num">Mult</th>
              <th className="num">Price</th>
              <th className="num">Proceeds</th>
              <th className="num">Comm</th>
              <th className="num">Cash flow</th>
              <th>Venue</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => (
              <tr key={e.id}>
                <td className="muted">{e.broker_trade_id}</td>
                <td>{e.symbol.trim()}</td>
                <td>{e.asset_class}</td>
                <td>{e.action}</td>
                <td>
                  {e.flags.map((f) => (
                    <span key={f} className="tag" style={{ marginRight: 3 }}>{f}</span>
                  ))}
                </td>
                <td className="muted">
                  {dateTime(e.executed_at)}
                  {e.out_of_window_warning && (
                    <span className="tag" style={{ marginLeft: 6, color: 'var(--amber)' }}>
                      out of window
                    </span>
                  )}
                </td>
                <td className="muted">{e.trading_day}</td>
                <td className="num">{num(e.quantity, 0)}</td>
                <td className="num muted">{num(e.multiplier, 0)}</td>
                <td className="num">{num(e.price)}</td>
                <td className="num">{money(e.proceeds)}</td>
                <td className="num neg">{money(e.commission)}</td>
                <td className={`num ${signClass(e.cash_flow)}`}>{money(e.cash_flow)}</td>
                <td className="muted">{e.exchange}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="footer-note">
        <code>proceeds</code> follows the TradeLog convention — signed cost, so a sell is negative.
        Cash flow is <code>−proceeds + commission</code>. A trading day for futures and futures
        options begins at 18:00 ET the prior calendar day.
      </div>
    </>
  )
}
