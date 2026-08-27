import { useEffect, useState } from 'react'
import { api, type SyncHealth } from './api'
import { dateTimeET } from './format'

/**
 * §3.8 — the guard against a silently dead sync.
 *
 * The failure this exists to prevent: the token expires, nightly runs fail
 * for three weeks, and the dashboard keeps showing stale numbers that look
 * entirely plausible. So this renders whenever the newest *successful* sync
 * is too old — not the newest attempt, which may be recent and failing.
 */
export function SyncBanner() {
  const [health, setHealth] = useState<SyncHealth | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .syncHealth()
        .then((h) => !cancelled && setHealth(h))
        .catch(() => undefined)
    load()
    const timer = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (!health) return null

  // A healthy scheduled setup gets a quiet one-liner rather than silence,
  // so "nothing is scheduled" is distinguishable from "all is well".
  if (!health.is_stale && !health.needs_attention) {
    if (!health.scheduler_running || !health.next_run_at) return null
    return (
      <div className="sync-status muted">
        Auto-sync on · next run {dateTimeET(health.next_run_at)}
        {health.last_success_at &&
          ` · last success ${dateTimeET(health.last_success_at)}`}
      </div>
    )
  }

  // Nothing to shout about on an install that has never attempted a sync —
  // file upload is a legitimate way to run this app.
  if (!health.last_attempt_at) return null

  const credentialProblem = health.needs_attention

  return (
    <div className={`banner ${credentialProblem ? 'err' : 'warn'}`} role="alert">
      {credentialProblem ? (
        <>
          <strong>Sync is failing and needs you.</strong> The last attempt returned error{' '}
          <code>{health.last_error_code}</code> ({health.last_status}). This class of failure is
          never retried automatically — a token or query setting has to be fixed before data can
          flow again.
        </>
      ) : (
        <>
          <strong>Data may be stale.</strong> The last successful sync was{' '}
          {health.business_days_since_success} business days ago, past the{' '}
          {health.staleness_threshold_business_days}-day threshold.
        </>
      )}{' '}
      {health.last_success_at ? (
        <>Last success: {dateTimeET(health.last_success_at)}.</>
      ) : (
        <>No sync has ever succeeded.</>
      )}
      {!health.scheduler_running && (
        <div style={{ marginTop: 6 }}>
          No sync is scheduled, so this will not recover on its own — the data will stay as
          old as it is until a sync is run.
        </div>
      )}
      {health.next_run_at && (
        <div style={{ marginTop: 6, opacity: 0.85 }}>
          Next scheduled attempt: {dateTimeET(health.next_run_at)}.
        </div>
      )}
      {health.last_error_message && (
        <div style={{ marginTop: 6, opacity: 0.85, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
          {health.last_error_message}
        </div>
      )}
    </div>
  )
}
