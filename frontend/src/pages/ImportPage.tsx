import { useRef, useState } from 'react'
import { api, type ImportReport } from '../api'
import { money } from '../format'

export function ImportPage({ onImported }: { onImported: () => void }) {
  const [report, setReport] = useState<ImportReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // `busy` state updates asynchronously, so two change events dispatched
  // before the next render would both get past it. A ref flips synchronously
  // and actually prevents the duplicate upload.
  const uploading = useRef(false)

  async function handleFile(file: File) {
    if (uploading.current) return
    uploading.current = true
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      setReport(await api.upload(file))
      onImported()
    } catch (e) {
      setError(String(e))
    } finally {
      uploading.current = false
      setBusy(false)
    }
  }

  const reconFailed = report && !report.reconciliation.passed

  return (
    <>
      <h1>Import</h1>
      <p className="subtitle">
        IBKR TradeLog (<code>.tlg</code>) or Activity Flex XML — the format is detected from the
        file. Re-importing the same window is safe; executions dedupe on the broker's own
        identifiers.
      </p>

      <div
        className="dropzone"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const f = e.dataTransfer.files[0]
          if (f) handleFile(f)
        }}
      >
        {busy ? 'Importing…' : 'Drop a .tlg or Flex .xml file here, or click to choose one'}
        <input
          ref={inputRef}
          type="file"
          accept=".tlg,.xml,text/plain,text/xml"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) handleFile(f)
          }}
        />
      </div>

      {error && <div className="banner err" style={{ marginTop: 16 }}>{error}</div>}

      {report && (
        <>
          <h2>Import report</h2>

          {reconFailed ? (
            <div className="banner err">
              <strong>Reconciliation failed.</strong> Computed positions disagree with the broker's
              own LOT snapshot. This almost always means a missing prior-window file — statistics
              built on this data will be wrong until it is resolved.
            </div>
          ) : (
            <div className="banner ok">
              <strong>Reconciliation passed.</strong> Every computed open position matches the
              broker's LOT records on quantity, and cost basis is within $0.01.
            </div>
          )}

          {report.closed_lot_check && (
            <div className={`banner ${report.closed_lot_check.passed ? 'ok' : 'err'}`}>
              <strong>
                Broker P&amp;L check {report.closed_lot_check.passed ? 'passed' : 'failed'}.
              </strong>{' '}
              {report.closed_lot_check.trades_matched} of {report.closed_lot_check.trades_checked}{' '}
              trades match IBKR's own FIFO realized P&amp;L to within $0.01, across{' '}
              {report.closed_lot_check.lots_total} closed lots (aggregate difference{' '}
              {money(report.closed_lot_check.aggregate_difference, 4)}). This validates the whole
              matching algorithm against the broker's books, not just the ending positions.
              {report.closed_lot_check.skipped_transfers > 0 && (
                <div style={{ marginTop: 8 }}>
                  {report.closed_lot_check.transfer_notes.map((n, i) => (
                    <div key={i} style={{ opacity: 0.85 }}>
                      {n}
                    </div>
                  ))}
                </div>
              )}
              {report.closed_lot_check.issues.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  {report.closed_lot_check.issues.map((n, i) => (
                    <div key={i}>{n}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="stat-grid">
            <div className="stat">
              <div className="label">Source format</div>
              <div className="value" style={{ fontSize: 15 }}>
                {report.source_format === 'FLEX_XML' ? 'Flex XML' : 'TradeLog'}
              </div>
            </div>
            <div className="stat">
              <div className="label">Executions parsed</div>
              <div className="value">{report.rows_parsed.executions}</div>
            </div>
            <div className="stat">
              <div className="label">Inserted</div>
              <div className="value">{report.rows_inserted}</div>
            </div>
            <div className="stat">
              <div className="label">Skipped as duplicate</div>
              <div className="value">{report.rows_duplicate}</div>
            </div>
            <div className="stat">
              <div className="label">Position records</div>
              <div className="value">{report.rows_parsed.positions}</div>
            </div>
            <div className="stat">
              <div className="label">Date range</div>
              <div className="value" style={{ fontSize: 14 }}>
                {report.date_range ? `${report.date_range[0]} → ${report.date_range[1]}` : '—'}
              </div>
            </div>
            <div className="stat">
              <div className="label">Account</div>
              <div className="value" style={{ fontSize: 14 }}>{report.account_id}</div>
            </div>
          </div>

          {report.warnings.length > 0 && (
            <>
              <h2>Warnings ({report.warnings.length})</h2>
              <div className="panel">
                {report.warnings.map((w, i) => (
                  <div key={i} style={{ padding: '5px 0', fontSize: 13, lineHeight: 1.5 }}>
                    {w}
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </>
  )
}
