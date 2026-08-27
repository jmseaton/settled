import { useCallback, useEffect, useState } from 'react'
import { api, type CashList } from '../api'
import { money, signClass } from '../format'

const TYPES = [
  'DEPOSIT',
  'WITHDRAWAL',
  'TRANSFER_IN',
  'TRANSFER_OUT',
  'DIVIDEND',
  'INTEREST',
  'FEE',
  'ADJUSTMENT',
] as const

const CSV_PLACEHOLDER = `date,type,amount,note
2025-11-05,DEPOSIT,5000,opening wire
2026-01-12,WITHDRAWAL,(250),transfer out
2026-02-03,DIVIDEND,4.25,`

const input = {
  background: 'var(--panel-2)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '6px 9px',
  fontSize: 13,
}

export function CashPage({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<CashList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const [date, setDate] = useState('')
  const [type, setType] = useState<string>('DEPOSIT')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')

  const [csv, setCsv] = useState('')
  const [showCsv, setShowCsv] = useState(false)

  const load = useCallback(() => {
    api.cashTransactions().then(setData).catch((e) => setError(String(e)))
  }, [])

  useEffect(load, [load])

  async function run(action: () => Promise<string>) {
    setError(null)
    setMessage(null)
    try {
      setMessage(await action())
      load()
      onChanged?.()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    }
  }

  if (error && !data) return <div className="banner err">{error}</div>
  if (!data) return <p className="muted">Loading…</p>

  const totals = data.totals_by_type

  return (
    <>
      <h1>Cash</h1>
      <p className="subtitle">
        Deposits, withdrawals, transfers, dividends, interest and fees (§7.1). External flows are
        what TWR strips out; dividends and fees are returns, not flows, and are counted
        separately.
      </p>

      {error && <div className="banner err">{error}</div>}
      {message && <div className="banner ok">{message}</div>}

      <div className="stat-grid">
        <div className="stat">
          <div className="label">Net external flow</div>
          <div className={`value ${signClass(data.net_external_flow)}`}>
            {money(data.net_external_flow)}
          </div>
          <div className="note">Deposits + transfers − withdrawals</div>
        </div>
        {Object.entries(totals)
          .sort()
          .map(([kind, value]) => (
            <div className="stat" key={kind}>
              <div className="label">{kind.replace('_', ' ')}</div>
              <div className={`value ${signClass(value)}`}>{money(value)}</div>
            </div>
          ))}
      </div>

      <h2>Add a transaction</h2>
      <div className="panel">
        <div className="toolbar">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace('_', ' ')}
              </option>
            ))}
          </select>
          <input
            type="text"
            inputMode="decimal"
            placeholder="Amount"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            style={{ ...input, width: 120 }}
          />
          <input
            type="text"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            style={{ ...input, width: 240 }}
          />
          <button
            className="primary"
            disabled={!date || !amount}
            onClick={() =>
              run(async () => {
                await api.createCashTransaction({ date, type, amount, note: note || undefined })
                setAmount('')
                setNote('')
                return 'Added. The equity curve has been rebuilt.'
              })
            }
          >
            Add
          </button>
          <button className="primary" onClick={() => setShowCsv((s) => !s)}>
            {showCsv ? 'Hide CSV paste' : 'Paste CSV'}
          </button>
        </div>
        <div className="muted" style={{ fontSize: 12 }}>
          Sign is normalized to the type, so a withdrawal entered as 500 and one entered as −500
          both reduce the account. Rows added here are marked <span className="tag">MANUAL</span>{' '}
          and survive every resync.
        </div>

        {showCsv && (
          <div style={{ marginTop: 14 }}>
            <textarea
              value={csv}
              onChange={(e) => setCsv(e.target.value)}
              placeholder={CSV_PLACEHOLDER}
              rows={7}
              style={{ ...input, width: '100%', fontFamily: 'ui-monospace, monospace' }}
            />
            <div className="toolbar" style={{ marginTop: 10 }}>
              <button
                className="primary"
                disabled={!csv.trim()}
                onClick={() =>
                  run(async () => {
                    const result = await api.pasteCashCsv(csv)
                    if (result.errors.length) {
                      throw new Error(
                        `Imported ${result.inserted} of ${result.rows_read}. ` +
                          result.errors.join(' · '),
                      )
                    }
                    setCsv('')
                    return `Imported ${result.inserted} row(s).`
                  })
                }
              >
                Import
              </button>
              <span className="muted">
                For pre-Flex history. A bad row is named rather than aborting the rest.
              </span>
            </div>
          </div>
        )}
      </div>

      <h2>Transactions ({data.total})</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th className="num">Amount</th>
              <th>Flow</th>
              <th>Source</th>
              <th>Note</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.items.map((row) => (
              <tr key={row.id}>
                <td>{row.occurred_on}</td>
                <td>{row.type.replace('_', ' ')}</td>
                <td className={`num ${signClass(row.amount)}`}>{money(row.amount)}</td>
                <td>
                  {row.is_external_flow ? (
                    <span className="tag">external</span>
                  ) : (
                    <span className="muted">return</span>
                  )}
                </td>
                <td>
                  <span className="tag">{row.source}</span>
                </td>
                <td
                  className="muted"
                  style={{ whiteSpace: 'normal', maxWidth: 380 }}
                  title={row.note ?? ''}
                >
                  {row.note}
                </td>
                <td>
                  {row.source === 'MANUAL' && (
                    <button
                      className="primary"
                      style={{ padding: '3px 9px', fontSize: 12 }}
                      onClick={() =>
                        run(async () => {
                          await api.deleteCashTransaction(row.id)
                          return 'Deleted.'
                        })
                      }
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="footer-note">
        {data.note} A broker row cannot be edited in place: the next sync would overwrite the
        edit without saying so. Enter an offsetting manual adjustment instead — that is the
        mechanism built for exactly this.
      </div>
    </>
  )
}
