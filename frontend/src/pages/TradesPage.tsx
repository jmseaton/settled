import { Fragment, useEffect, useMemo, useState } from 'react'
import { api, type Trade, type TradeGroup } from '../api'
import { dateTime, duration, money, num, pct, signClass } from '../format'

type SortKey = keyof Trade

export function TradesPage({ mode }: { mode: string }) {
  const [trades, setTrades] = useState<Trade[]>([])
  const [groups, setGroups] = useState<TradeGroup[]>([])
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [policy, setPolicy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [book, setBook] = useState('')
  const [status, setStatus] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('opened_at')
  const [asc, setAsc] = useState(false)
  const [busy, setBusy] = useState(false)
  // §10.3 — the selection is the interesting part; every bulk action
  // shares it, so it lives here rather than in each action's own control.
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkAction, setBulkAction] = useState('add_tag')
  const [bulkValue, setBulkValue] = useState('')
  const [bulkResult, setBulkResult] = useState<string | null>(null)

  function load() {
    api
      .trades()
      .then((r) => {
        setTrades(r.items)
        setPolicy(r.transfer_basis_policy)
      })
      .catch((e) => setError(String(e)))
    api.groups().then((r) => setGroups(r.items)).catch(() => undefined)
  }

  useEffect(load, [])

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function runBulk() {
    setBusy(true)
    setBulkResult(null)
    setError(null)
    try {
      const ids = [...selected]
      const payload: Record<string, unknown> = { trade_ids: ids, action: bulkAction }
      if (bulkAction === 'add_tag') payload.name = bulkValue
      if (bulkAction === 'set_levels') payload.stop_loss = bulkValue
      if (bulkAction === 'add_note') payload.body_md = bulkValue
      const result = await api.bulkTrades(payload as Parameters<typeof api.bulkTrades>[0])
      setBulkResult(
        `${result.applied} of ${result.selected} updated.` +
          (result.skipped.length ? ` ${result.skipped.length} skipped — ${result.note}` : ''),
      )
      setBulkValue('')
      setSelected(new Set())
      load()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  async function changePolicy(next: string) {
    setBusy(true)
    try {
      await api.setTransferBasisPolicy(next)
      load()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const rows = useMemo(() => {
    const filtered = trades.filter(
      (t) => (!book || t.book === book) && (!status || t.status === status),
    )
    return [...filtered].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey]
      if (av === null) return 1
      if (bv === null) return -1
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv))
      return asc ? cmp : -cmp
    })
  }, [trades, book, status, sortKey, asc])

  function sortBy(k: SortKey) {
    if (k === sortKey) setAsc(!asc)
    else { setSortKey(k); setAsc(false) }
  }

  const th = (k: SortKey, label: string, cls = '') => (
    <th className={cls} style={{ cursor: 'pointer' }} onClick={() => sortBy(k)}>
      {label}{sortKey === k ? (asc ? ' ▲' : ' ▼') : ''}
    </th>
  )

  if (error) return <div className="banner err">{error}</div>

  const books = Array.from(new Set(trades.map((t) => t.book).filter(Boolean))) as string[]
  const hasOrphan = rows.some((t) => t.status === 'ORPHAN_CLOSE')
  const transferred = rows.filter((t) => t.origin === 'TRANSFERRED')

  return (
    <>
      <h1>Trades</h1>
      <p className="subtitle">
        Round trips reconstructed from raw executions by FIFO lot matching.{' '}
        {mode === 'STRATEGY'
          ? 'Grouped legs are collapsed into one strategy row — expand to see them.'
          : 'One row per instrument round trip, so a vertical appears as its two legs.'}
      </p>

      <div className="toolbar">
        <select value={book} onChange={(e) => setBook(e.target.value)}>
          <option value="">All books</option>
          {books.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="CLOSED">Closed</option>
          <option value="OPEN">Open</option>
          <option value="ORPHAN_CLOSE">Orphan close</option>
        </select>
        <span className="muted">
          {mode === 'STRATEGY'
            ? `${groups.filter((g) => !book || g.book === book).length} strategies across ${rows.length} legs`
            : `${rows.length} of ${trades.length} trades`}
        </span>
        {transferred.length > 0 && (
          <>
            <label className="muted" style={{ marginLeft: 'auto' }}>Transfer basis</label>
            <select value={policy} disabled={busy} onChange={(e) => changePolicy(e.target.value)}>
              <option value="TRANSFER_MARK">TRANSFER_MARK (default)</option>
              <option value="ORIGINAL_COST">ORIGINAL_COST</option>
            </select>
          </>
        )}
      </div>

      {selected.size > 0 && (
        <div className="toolbar" style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 8, padding: 10 }}>
          <strong>{selected.size} selected</strong>
          <select value={bulkAction} onChange={(e) => setBulkAction(e.target.value)}>
            <option value="add_tag">Add tag</option>
            <option value="set_levels">Set stop loss</option>
            <option value="add_note">Add note</option>
          </select>
          <input
            type="text"
            value={bulkValue}
            onChange={(e) => setBulkValue(e.target.value)}
            placeholder={
              bulkAction === 'add_tag' ? 'Tag name' : bulkAction === 'set_levels' ? 'Stop price' : 'Note'
            }
            style={{
              background: 'var(--panel-2)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              padding: '6px 9px',
              fontSize: 13,
              minWidth: 220,
            }}
          />
          <button className="primary" disabled={busy || !bulkValue.trim()} onClick={runBulk}>
            Apply
          </button>
          <button className="primary" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {bulkResult && <div className="banner ok">{bulkResult}</div>}

      {transferred.length > 0 && (
        <div className="banner info">
          <strong>
            {transferred.length} transferred position
            {transferred.length === 1 ? '' : 's'}, valued under {policy}.
          </strong>{' '}
          {policy === 'TRANSFER_MARK' ? (
            <>
              Basis is the market value on the transfer date, so P&amp;L reflects only what happened
              while the position was in this account. This matches IBKR's performance methodology,
              which backs pre-transfer gains out of its own figures — but it means trade P&amp;L
              will <em>not</em> equal IBKR's realized-gain reporting.
            </>
          ) : (
            <>
              Basis is the delivering broker's acquisition cost, so P&amp;L matches IBKR's realized
              figures and includes gains earned before the position arrived — under different
              decisions, at another broker.
            </>
          )}{' '}
          Transferred trades are excluded from duration statistics under either policy.
        </div>
      )}

      {hasOrphan && (
        <div className="banner warn">
          An <strong>orphan close</strong> is a position closed with no opening execution in the
          data — typically a transferred-in position or a missing earlier file. No P&amp;L is
          attributed to it, and it is excluded from win rate and duration statistics.
        </div>
      )}

      <div className="scroll">
        <table>
          <thead>
            <tr>
              {/* §10.3 — multi-select drives every bulk action. */}
              <th style={{ width: 28 }} />
              {th('symbol', 'Symbol')}
              {th('asset_class', 'Class')}
              {th('side', 'Side')}
              {th('status', 'Status')}
              {th('book', 'Book')}
              {th('opened_at', 'Opened')}
              {th('closed_at', 'Closed')}
              {th('duration_seconds', 'Duration', 'num')}
              {th('open_qty', 'Qty', 'num')}
              {th('avg_open_price', 'Open', 'num')}
              {th('avg_close_price', 'Close', 'num')}
              {th('gross_pnl', 'Gross', 'num')}
              {th('commissions', 'Comm', 'num')}
              {th('net_pnl', 'Net P&L', 'num')}
              {th('return_pct', 'Return', 'num')}
              {th('pnl_points', 'Points', 'num')}
              {th('execution_count', 'Execs', 'num')}
            </tr>
          </thead>
          <tbody>
            {mode === 'STRATEGY' &&
              groups
                .filter(
                  (g) =>
                    (!book || g.book === book) &&
                    g.leg_count > 1 &&
                    // §5.4 — a chain is a *second* view over trades that
                    // already belong to their own spread, so it owns no
                    // legs of its own and would render as an empty parent
                    // here. Chains live on Strategies, where their
                    // frozen-risk and split-duration figures belong.
                    g.strategy_type !== 'ASSIGNMENT_CHAIN',
                )
                .map((g) => {
                  const open = expanded.has(g.id)
                  return (
                    <Fragment key={`g${g.id}`}>
                      <tr
                        onClick={() =>
                          setExpanded((prev) => {
                            const next = new Set(prev)
                            next.has(g.id) ? next.delete(g.id) : next.add(g.id)
                            return next
                          })
                        }
                        style={{ cursor: 'pointer' }}
                      >
                        <td />
                        <td>
                          <span className="muted">{open ? '▾ ' : '▸ '}</span>
                          {g.underlying} {g.strategy_type.replace(/_/g, ' ').toLowerCase()}
                        </td>
                        <td>{g.legs.length} legs</td>
                        <td>—</td>
                        <td>{g.closed_at ? 'CLOSED' : 'OPEN'}</td>
                        <td className="muted">{g.book}</td>
                        <td className="muted">{dateTime(g.opened_at)}</td>
                        <td className="muted">{dateTime(g.closed_at)}</td>
                        <td className="num">—</td>
                        <td className="num">—</td>
                        <td className="num">—</td>
                        <td className="num">—</td>
                        <td className={`num ${signClass(g.gross_pnl)}`}>{money(g.gross_pnl)}</td>
                        <td className={`num ${signClass(g.commissions)}`}>{money(g.commissions)}</td>
                        <td className={`num ${signClass(g.net_pnl)}`}>{money(g.net_pnl)}</td>
                        <td className="num">
                          {g.return_on_risk === null ? '—' : pct(g.return_on_risk)}
                        </td>
                        <td className="num">—</td>
                        <td className="num">—</td>
                      </tr>
                      {open &&
                        g.legs.map((leg) => {
                          const t = trades.find((x) => x.id === leg.trade_id)
                          if (!t) return null
                          return (
                            <tr key={`g${g.id}-l${leg.trade_id}`} className="sub-row">
                              <td style={{ paddingLeft: 12 }}>
                                <input
                                  type="checkbox"
                                  checked={selected.has(t.id)}
                                  onChange={() => toggleSelected(t.id)}
                                />
                              </td>
                              <td>{t.symbol.trim()}</td>
                              <td>{t.asset_class}</td>
                              <td>{t.side}</td>
                              <td>{t.status}</td>
                              <td className="muted">{t.book}</td>
                              <td className="muted">{dateTime(t.opened_at)}</td>
                              <td className="muted">{dateTime(t.closed_at)}</td>
                              <td className="num">{duration(t.duration_seconds)}</td>
                              <td className="num">{num(t.open_qty, 0)}</td>
                              <td className="num">{num(t.avg_open_price)}</td>
                              <td className="num">{num(t.avg_close_price)}</td>
                              <td className={`num ${signClass(t.gross_pnl)}`}>{money(t.gross_pnl)}</td>
                              <td className={`num ${signClass(t.commissions)}`}>{money(t.commissions)}</td>
                              <td className={`num ${signClass(t.net_pnl)}`}>{money(t.net_pnl)}</td>
                              <td className="num">{t.return_pct === null ? '—' : pct(t.return_pct)}</td>
                              <td className="num">{num(t.pnl_points)}</td>
                              <td className="num">{t.execution_count}</td>
                            </tr>
                          )
                        })}
                    </Fragment>
                  )
                })}
            {rows
              .filter((t) => {
                if (mode !== 'STRATEGY') return true
                // In strategy mode the multi-leg rows are rendered above;
                // only ungrouped positions remain to be listed flat.
                const g = groups.find((x) => x.legs.some((l) => l.trade_id === t.id))
                return !g || g.leg_count === 1
              })
              .map((t) => (
              <tr key={t.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(t.id)}
                    onChange={() => toggleSelected(t.id)}
                    title={
                      t.has_synthetic_leg
                        ? 'Reconstructed opening leg — journal data cannot be anchored to it (§5.5)'
                        : 'Select for bulk actions'
                    }
                  />
                </td>
                <td>{t.symbol.trim()}</td>
                <td>{t.asset_class}</td>
                <td>{t.side}</td>
                <td>
                  {t.status === 'ORPHAN_CLOSE' ? <span className="tag">orphan</span> : t.status}
                  {t.origin === 'TRANSFERRED' && (
                    <span
                      className="tag"
                      style={{ marginLeft: 4, color: 'var(--accent)' }}
                      title="Opening leg reconstructed from broker transfer records, not a real fill"
                    >
                      transferred
                    </span>
                  )}
                </td>
                <td className="muted">{t.book}</td>
                <td className="muted">{dateTime(t.opened_at)}</td>
                <td className="muted">{dateTime(t.closed_at)}</td>
                <td className="num">
                  {t.excluded_from_duration ? <span className="muted">excl.</span> : duration(t.duration_seconds)}
                </td>
                <td className="num">{num(t.open_qty, 0)}</td>
                <td className="num">
                  {num(t.avg_open_price)}
                  {t.has_synthetic_leg && (
                    <span
                      className="muted"
                      title="Synthesized from broker records — not a real execution"
                      style={{ marginLeft: 4 }}
                    >
                      ⁑
                    </span>
                  )}
                </td>
                <td className="num">{num(t.avg_close_price)}</td>
                <td className={`num ${signClass(t.gross_pnl)}`}>{money(t.gross_pnl)}</td>
                <td className="num neg">{money(t.commissions)}</td>
                <td className={`num ${signClass(t.net_pnl)}`}>{money(t.net_pnl)}</td>
                <td className={`num ${signClass(t.return_pct)}`}>{pct(t.return_pct)}</td>
                <td className="num">{num(t.pnl_points)}</td>
                <td className="num muted">{t.execution_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="footer-note">
        P&amp;L is realized only. An open or partially closed trade reports the result of the
        portion actually closed; no mark-to-market is applied, since v1 has no market data feed.
        {' '}An open price marked <span className="muted">⁑</span> comes from a synthesized opening
        leg reconstructed from broker transfer or closed-lot records, never from a real fill.
      </div>
    </>
  )
}
