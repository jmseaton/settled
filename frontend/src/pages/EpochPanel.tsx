import { useEffect, useState } from 'react'
import { api, type Epoch } from '../api'
import { money } from '../format'

/**
 * §0.3 — the performance epoch, and the cross-check beside it.
 *
 * Two fields that silently determine every percentage in the app, which is
 * why the IBKR figure sits next to the stored one rather than in a tooltip:
 * a wrong starting value produces returns that look entirely plausible and
 * are wrong, and that is the exact failure this app exists to prevent.
 */
export function EpochPanel({ epoch, onChange }: { epoch: Epoch; onChange: () => void }) {
  const [open, setOpen] = useState(!epoch.configured)
  const [date, setDate] = useState(epoch.tracking_start_date ?? '')
  const [value, setValue] = useState(
    epoch.tracking_start_value !== null ? String(epoch.tracking_start_value) : '',
  )
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDate(epoch.tracking_start_date ?? '')
    setValue(epoch.tracking_start_value !== null ? String(epoch.tracking_start_value) : '')
  }, [epoch.tracking_start_date, epoch.tracking_start_value])

  async function save() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const result = await api.setEpoch({
        tracking_start_date: date || null,
        tracking_start_value: value || null,
      })
      setMessage(result.rebuild_note ?? null)
      onChange()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  async function fetchFromIbkr() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const result = await api.fetchEpochValue(date || undefined)
      setValue(String(result.fetched_value))
      setMessage(`IBKR reports ${money(result.fetched_value)} for ${date}.`)
      onChange()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  const divergent = epoch.divergence !== null && Math.abs(epoch.divergence) > 0.01

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <strong>Performance epoch</strong>{' '}
          <span className="muted">
            {epoch.configured
              ? `measuring from ${epoch.tracking_start_date} at ${money(epoch.tracking_start_value)}`
              : 'not set'}
          </span>
        </div>
        <button className="primary" onClick={() => setOpen((o) => !o)}>
          {open ? 'Close' : 'Edit'}
        </button>
      </div>

      {divergent && (
        <div className="banner warn" style={{ marginTop: 12, marginBottom: 0 }}>
          <strong>Starting value disagrees with IBKR.</strong> Stored{' '}
          {money(epoch.tracking_start_value)}, IBKR reports{' '}
          {money(epoch.tracking_start_value_ibkr)} — a gap of {money(epoch.divergence)}. Every
          percentage on this page is computed from the stored figure.
        </div>
      )}

      {open && (
        <div style={{ marginTop: 14 }}>
          <p className="muted" style={{ fontSize: 12, marginTop: 0, lineHeight: 1.6 }}>
            Performance is measured from when the current strategy started, not from account
            funding — early experimentation would otherwise contaminate every statistic with no
            way to un-mix it later. Pre-epoch executions are still imported: they supply the cost
            basis of positions open on day one. <strong>Changing this rebuilds everything.</strong>
          </p>

          <div className="toolbar">
            <label className="muted">
              Start date{' '}
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
            <label className="muted">
              Account value{' '}
              <input
                type="text"
                inputMode="decimal"
                value={value}
                placeholder="905.00"
                onChange={(e) => setValue(e.target.value)}
                style={{
                  background: 'var(--panel-2)',
                  color: 'var(--text)',
                  border: '1px solid var(--border)',
                  borderRadius: 6,
                  padding: '6px 9px',
                  fontSize: 13,
                  width: 130,
                }}
              />
            </label>
            <button className="primary" onClick={fetchFromIbkr} disabled={busy || !date}>
              Fetch from IBKR
            </button>
            <button className="primary" onClick={save} disabled={busy}>
              Save &amp; rebuild
            </button>
          </div>

          <div className="muted" style={{ fontSize: 12 }}>
            Fetch rather than type: a hand-entered NAV is an error vector that silently skews
            every percentage in the app. IBKR's own figure for this date:{' '}
            <strong>
              {epoch.tracking_start_value_ibkr !== null
                ? money(epoch.tracking_start_value_ibkr)
                : 'not fetched yet'}
            </strong>
            .
          </div>

          {message && <div className="banner ok" style={{ marginTop: 12 }}>{message}</div>}
          {error && <div className="banner err" style={{ marginTop: 12 }}>{error}</div>}
        </div>
      )}
    </div>
  )
}
