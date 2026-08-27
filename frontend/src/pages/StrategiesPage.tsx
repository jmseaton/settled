import { Fragment, useEffect, useState } from 'react'
import { api, type RiskStats, type TradeGroup } from '../api'
import { dateTime, duration, money, num, pct } from '../format'

const SOURCE_LABEL: Record<string, string> = {
  BROKERAGE_ORDER_ID: 'combo order',
  TIMESTAMP: 'inferred',
  SINGLE: 'single leg',
}

export function StrategiesPage() {
  const [groups, setGroups] = useState<TradeGroup[]>([])
  const [risk, setRisk] = useState<RiskStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [book, setBook] = useState('Options Income')
  const [expanded, setExpanded] = useState<number | null>(null)

  useEffect(() => {
    api.groups().then((r) => setGroups(r.items)).catch((e) => setError(String(e)))
  }, [])
  useEffect(() => {
    api.riskStats(book || undefined).then(setRisk).catch((e) => setError(String(e)))
  }, [book])

  if (error) return <div className="banner err">{error}</div>

  const visible = groups.filter((g) => g.strategy_type !== 'ASSIGNMENT_CHAIN')
  const rows = book ? visible.filter((g) => g.book === book) : visible
  // §5.4 — a chain spans trades that already belong to their own spreads,
  // so it is a second view rather than another strategy. Listing it beside
  // them would double-count every leg it covers.
  const chains = groups.filter(
    (g) => g.strategy_type === 'ASSIGNMENT_CHAIN' && (!book || g.book === book),
  )
  const books = Array.from(new Set(visible.map((g) => g.book).filter(Boolean))) as string[]
  const inferred = rows.filter((g) => g.detection_source === 'TIMESTAMP').length

  return (
    <div>
      <h1>Strategies</h1>
      <p className="subtitle">
        Multi-leg positions grouped into one analytical unit. A vertical's legs are
        individually meaningless — <code>ACME 13AUG31 172 P</code> reads −$61.37 and its
        180.6 counterpart +$103.75; the spread made +$42.37.
      </p>

      <div className="toolbar">
        <select value={book} onChange={(e) => setBook(e.target.value)}>
          <option value="">All books</option>
          {books.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <span className="muted">
          {rows.length} of {groups.length} strategies
          {inferred > 0 && ` · ${inferred} inferred from timestamps`}
        </span>
      </div>

      {risk && (
        <>
          <h2>Return on risk, by strategy type</h2>
          <div className="banner info">
            <strong>Not pooled, deliberately.</strong> {risk.refusal_reason}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th className="num">Groups</th>
                  <th className="num">Net P&amp;L</th>
                  <th className="num">Total max risk</th>
                  <th className="num">Return on risk</th>
                  <th className="num">Win rate</th>
                </tr>
              </thead>
              <tbody>
                {risk.segments.map((s) => (
                  <tr key={s.strategy_type}>
                    <td>{s.strategy_type}</td>
                    <td className="num">{s.group_count}</td>
                    <td className={`num ${s.net_pnl >= 0 ? 'pos' : 'neg'}`}>{money(s.net_pnl)}</td>
                    <td className="num">
                      {s.total_max_risk === null ? (
                        <span className="muted">unbounded</span>
                      ) : (
                        money(s.total_max_risk)
                      )}
                      {s.unbounded_risk_count > 0 && s.total_max_risk !== null && (
                        <span className="muted" title="Groups with unbounded risk, excluded from the ratio">
                          {' '}
                          +{s.unbounded_risk_count} unbounded
                        </span>
                      )}
                    </td>
                    <td className="num">
                      {s.return_on_risk === null ? <span className="muted">—</span> : pct(s.return_on_risk)}
                    </td>
                    <td className="num">{s.win_rate === null ? '—' : pct(s.win_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {chains.length > 0 && (
        <>
          <h2>Assignment chains</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Underlying</th>
                  <th>Opened</th>
                  <th>Closed</th>
                  <th className="num">Net P&amp;L</th>
                  <th className="num">Risk at open</th>
                  <th className="num">RoR at open</th>
                  <th className="num">Capital after</th>
                  <th className="num">RoR after</th>
                  <th className="num">Option held</th>
                  <th className="num">Underlying held</th>
                </tr>
              </thead>
              <tbody>
                {chains.map((g) => (
                  <tr key={g.id}>
                    <td>{g.underlying ?? '—'}</td>
                    <td>{dateTime(g.opened_at)}</td>
                    <td>{g.closed_at ? dateTime(g.closed_at) : <span className="muted">open</span>}</td>
                    <td className={`num ${g.net_pnl >= 0 ? 'pos' : 'neg'}`}>{money(g.net_pnl)}</td>
                    <td className="num">
                      {g.max_risk === null ? <span className="muted">—</span> : money(g.max_risk)}
                    </td>
                    <td className="num">{pct(g.return_on_risk)}</td>
                    <td className="num">{money(g.post_assignment_capital)}</td>
                    <td className="num">{pct(g.return_on_post_assignment_capital)}</td>
                    <td className="num">{duration(g.option_duration_seconds)}</td>
                    <td className="num">{duration(g.underlying_duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="footer-note" style={{ marginTop: 10, borderTop: 'none', paddingTop: 0 }}>
            The spread's defined risk stopped applying the moment a leg was assigned, so{' '}
            <strong>risk at open is frozen</strong> and the capital the resulting position
            actually tied up is reported beside it. Both return figures are shown and never
            averaged: they measure different commitments. Durations are split for the same reason
            — the option leg and the position it became were held over different windows, and one
            combined figure would describe neither (§5.4).
          </div>
        </>
      )}

      <h2>Strategies</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Strategy</th>
              <th>Underlying</th>
              <th className="num">Legs</th>
              <th>Opened</th>
              <th>Closed</th>
              <th className="num">DTE</th>
              <th className="num">Credit/debit</th>
              <th className="num">Max risk</th>
              <th className="num">Net P&amp;L</th>
              <th className="num">RoR</th>
              <th>Detected</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((g) => (
              <Fragment key={g.id}>
                <tr
                  onClick={() => setExpanded(expanded === g.id ? null : g.id)}
                  style={{ cursor: g.leg_count > 1 ? 'pointer' : 'default' }}
                >
                  <td className="muted">{g.leg_count > 1 ? (expanded === g.id ? '▾' : '▸') : ''}</td>
                  <td>{g.strategy_type}</td>
                  <td>{g.underlying ?? '—'}</td>
                  <td className="num">{g.leg_count}</td>
                  <td>{dateTime(g.opened_at)}</td>
                  <td>{g.closed_at ? dateTime(g.closed_at) : <span className="muted">—</span>}</td>
                  <td className="num">{g.dte_at_open ?? '—'}</td>
                  <td className={`num ${g.net_debit_credit >= 0 ? 'pos' : 'neg'}`}>
                    {money(g.net_debit_credit)}
                  </td>
                  <td className="num">
                    {g.max_risk === null ? (
                      <span className="neg" title="Risk is unbounded — never shown as zero or blank">
                        unbounded
                      </span>
                    ) : (
                      money(g.max_risk)
                    )}
                  </td>
                  <td className={`num ${g.net_pnl >= 0 ? 'pos' : 'neg'}`}>{money(g.net_pnl)}</td>
                  <td className="num">
                    {g.return_on_risk === null ? <span className="muted">—</span> : pct(g.return_on_risk)}
                  </td>
                  <td>
                    <span
                      className="tag"
                      title={
                        g.detection_source === 'TIMESTAMP'
                          ? 'Inferred from an exact matching timestamp — the broker recorded no combo order id'
                          : g.detection_source === 'BROKERAGE_ORDER_ID'
                            ? "Grouped by the broker's own combo order id"
                            : 'A single-leg position, not a combo'
                      }
                    >
                      {SOURCE_LABEL[g.detection_source] ?? g.detection_source}
                    </span>
                  </td>
                </tr>
                {expanded === g.id &&
                  g.legs.map((leg) => (
                    <tr key={`${g.id}-${leg.trade_id}`} className="sub-row">
                      <td></td>
                      <td colSpan={3} className="muted">
                        {leg.symbol.trim()}
                      </td>
                      <td colSpan={2} className="muted">
                        {leg.side} {leg.right ?? ''} {leg.strike !== null ? num(leg.strike) : ''}
                      </td>
                      <td colSpan={4}></td>
                      <td className={`num ${leg.net_pnl >= 0 ? 'pos' : 'neg'}`}>{money(leg.net_pnl)}</td>
                      <td></td>
                    </tr>
                  ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="footer-note">
        Combos are grouped by the broker's own combo order id where it exists. The{' '}
        <span className="tag">inferred</span> ones matched on an exact opening timestamp, same
        underlying, and opposing directions — a same-day match would fabricate spreads out of
        unrelated positions, so it is deliberately strict. Max risk of{' '}
        <span className="neg">unbounded</span> is a real value, not missing data: a naked short
        call has no ceiling, and it is never counted as zero.
      </div>
    </div>
  )
}
