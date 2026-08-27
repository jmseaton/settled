import { useEffect, useRef, useState } from 'react'
import { api, type AuthStatus } from './api'

/**
 * §1.3a — the only screen a signed-out browser can reach.
 *
 * There is no username field, no "forgot password" and no account creation,
 * because there is one owner and the password lives in `.env`. Recovery is
 * editing that file and restarting, which is a runbook step rather than a
 * flow to build.
 */
export function LoginPage({ onSignedIn }: { onSignedIn: (status: AuthStatus) => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    input.current?.focus()
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await api.login(password))
    } catch (err) {
      // The backend's messages are already the ones worth showing: an
      // incorrect password, or the throttle saying how many seconds are
      // left. Re-wording them here would only lose the countdown.
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
      setPassword('')
      input.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="brand">
          Settled
          <small>Trade performance tracker</small>
        </div>

        <label className="login-field">
          <span>Password</span>
          <input
            ref={input}
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </label>

        {error && (
          <div className="banner err" role="alert">
            {error}
          </div>
        )}

        <button className="primary" type="submit" disabled={busy || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="login-note">
          The password is whatever <code>SETTLED_AUTH_PASSWORD_HASH</code> was generated from.
          Lost it? Generate a new hash with <code>python -m app.auth</code>, put it in{' '}
          <code>.env</code>, and restart.
        </div>
      </form>
    </div>
  )
}
