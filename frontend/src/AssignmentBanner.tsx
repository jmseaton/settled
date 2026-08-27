import { useCallback, useEffect, useState } from 'react'
import { api, type AssignmentAlert } from './api'

/**
 * §5.4 — "the single highest-value notification in the app; do not bury it
 * in a warnings list."
 *
 * An unexpected futures or stock position is the thing you want to be told
 * about rather than discover, and assignment lands in the statement *after*
 * the option's last trading day — so it is always at least a day old by the
 * time this can show it. It stays up until dismissed, and the dismissal
 * survives resync: re-raising a banner every night is how the one that
 * matters gets ignored.
 *
 * Expirations never appear here. They were on the calendar, Expiry Watch
 * forecast them, and bannering ten of them teaches the user to click
 * through on sight.
 */
export function AssignmentBanner({ dataVersion }: { dataVersion: number }) {
  const [alert, setAlert] = useState<AssignmentAlert | null>(null)

  const load = useCallback(() => {
    api.assignmentAlerts().then(setAlert).catch(() => setAlert(null))
  }, [])

  useEffect(load, [load, dataVersion])

  if (!alert || alert.needs_attention === 0) return null

  return (
    <div className="banner err" style={{ borderWidth: 2 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <strong>
            {alert.needs_attention} assignment
            {alert.needs_attention === 1 ? '' : 's'} or exercise
            {alert.needs_attention === 1 ? '' : 's'} to review.
          </strong>{' '}
          {alert.note}
        </div>
      </div>
      <ul style={{ margin: '10px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>
        {alert.items.map((item) => (
          <li key={item.id}>
            <strong>{item.event_type === 'ASSIGNMENT' ? 'Assigned' : 'Exercised'}</strong>{' '}
            {Math.abs(item.quantity)} × {item.symbol} on {item.occurred_on}
            {item.underlying && <> → position in {item.underlying}</>}
            {!item.linked && (
              <span className="muted"> · {item.link_note}</span>
            )}{' '}
            <button
              className="primary"
              style={{ padding: '2px 8px', fontSize: 11, marginLeft: 6 }}
              onClick={() => api.acknowledgeOptionEvent(item.id).then(load)}
            >
              Acknowledge
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
