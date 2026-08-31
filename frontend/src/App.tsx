import { useCallback, useEffect, useState } from 'react'
import { api, onUnauthorized, type AuthStatus } from './api'
import { LoginPage } from './LoginPage'
import { Logo } from './Logo'
import { AssignmentBanner } from './AssignmentBanner'
import { SyncBanner } from './SyncBanner'
import { CalendarPage } from './pages/CalendarPage'
import { DashboardPage } from './pages/DashboardPage'
import { CashPage } from './pages/CashPage'
import { ChartsPage } from './pages/ChartsPage'
import { ExecutionsPage } from './pages/ExecutionsPage'
import { ExpiryWatchPage } from './pages/ExpiryWatchPage'
import { ImportPage } from './pages/ImportPage'
import { JournalPage } from './pages/JournalPage'
import { PerformancePage } from './pages/PerformancePage'
import { StatsPage } from './pages/StatsPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { TrendPage } from './pages/TrendPage'
import { TradesPage } from './pages/TradesPage'

const PAGES = [
  'Import',
  'Dashboard',
  'Performance',
  'Statistics',
  'Charts',
  'Trend',
  'Calendar',
  'Strategies',
  'Trades',
  'Executions',
  'Cash',
  'Journal',
  'Expiry Watch',
] as const
type Page = (typeof PAGES)[number]

export default function App() {
  const [page, setPage] = useState<Page>('Dashboard')
  const [dataVersion, setDataVersion] = useState(0)
  // §6.4 — global, because every chart, stat and table respects it. Held
  // here so switching it re-renders every data page at once.
  const [mode, setMode] = useState<string>('STRATEGY')
  // §1.3a — undefined until /api/auth/session answers. Rendering the app
  // during that gap would fire a dozen data requests that 401, and
  // rendering the login form would flash it at an already-signed-in user.
  const [auth, setAuth] = useState<AuthStatus | undefined>()

  useEffect(() => {
    api.session().then(setAuth).catch(() =>
      // The session endpoint is the one call that should always answer. If
      // it does not, the backend is down — and the login form would be a
      // lie, so assume the open deployment the app has always been and let
      // the pages show their own errors.
      setAuth({ enabled: false, authenticated: true, session_expires_at: null }),
    )
  }, [])

  // Any request may come back 401 once the session ages out mid-session.
  const expired = useCallback(
    () => setAuth({ enabled: true, authenticated: false, session_expires_at: null }),
    [],
  )
  useEffect(() => {
    onUnauthorized(expired)
    return () => onUnauthorized(null)
  }, [expired])

  const signedIn = auth?.authenticated === true

  useEffect(() => {
    if (!signedIn) return
    api.settings().then((s) => setMode(s.analysis_mode)).catch(() => undefined)
  }, [signedIn])

  async function signOut() {
    try {
      await api.logout()
    } finally {
      // Sign out locally whatever the server said. A logout that fails and
      // leaves the app looking signed in is the worst of both.
      setAuth({ enabled: true, authenticated: false, session_expires_at: null })
      setDataVersion((v) => v + 1)
    }
  }

  async function changeMode(next: string) {
    setMode(next)
    try {
      await api.setAnalysisMode(next)
    } finally {
      setDataVersion((v) => v + 1)
    }
  }

  // Nothing renders until the session question is settled, and the login
  // form is the whole UI while it is unanswered — there is no partial state
  // where a signed-out browser sees a nav bar it cannot use.
  if (auth === undefined) return null
  if (!signedIn) return <LoginPage onSignedIn={setAuth} />

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">
          <span className="brand-name">
            <Logo size={26} />
            Settled
          </span>
          <small>Trade performance tracker</small>
        </div>
        <div className="nav">
          {PAGES.map((p) => (
            <button key={p} className={p === page ? 'active' : ''} onClick={() => setPage(p)}>
              {p}
            </button>
          ))}
        </div>

        <div className="mode-switch">
          <div className="mode-label">Analyze by</div>
          {(['STRATEGY', 'LEG'] as const).map((m) => (
            <button
              key={m}
              className={m === mode ? 'active' : ''}
              onClick={() => changeMode(m)}
              title={
                m === 'STRATEGY'
                  ? 'A vertical counts as one outcome — the decision you actually made'
                  : 'A vertical counts as one win and one loss, leg by leg'
              }
            >
              {m === 'STRATEGY' ? 'Strategy' : 'Leg'}
            </button>
          ))}
          <div className="mode-note">
            Totals are the same either way; win rate, expectancy and counts are not.
          </div>
        </div>

        {auth.enabled && (
          <div className="session-box">
            <button onClick={signOut}>Sign out</button>
          </div>
        )}
      </nav>

      <main className="main">
        {/* §1.3a — an install with no password is a legitimate choice on a
            trusted box, but never an invisible one. */}
        {!auth.enabled && (
          <div className="banner warn" role="alert">
            <strong>No password is set.</strong> Anyone who can reach this address can read
            the account's full position and P&amp;L history and trigger a sync. Set{' '}
            <code>SETTLED_AUTH_PASSWORD_HASH</code> in <code>.env</code> — generate one with{' '}
            <code>docker compose exec backend python -m app.auth</code> — or bind the port to
            localhost.
          </div>
        )}
        <SyncBanner />
        {/* §5.4 — above everything, on every page, until acknowledged. */}
        <AssignmentBanner dataVersion={dataVersion} />
        {/* Data pages remount on dataVersion so a fresh import is reflected.
            Import itself must not, or it would discard the report it just showed. */}
        {page === 'Import' && <ImportPage onImported={() => setDataVersion((v) => v + 1)} />}
        {page === 'Performance' && <PerformancePage key={dataVersion} />}
        {page === 'Statistics' && <StatsPage key={`${dataVersion}-${mode}`} mode={mode} />}
        {page === 'Dashboard' && <DashboardPage key={`${dataVersion}-${mode}`} mode={mode} />}
        {page === 'Charts' && <ChartsPage key={`${dataVersion}-${mode}`} mode={mode} />}
        {page === 'Trend' && <TrendPage key={`${dataVersion}-${mode}`} mode={mode} />}
        {page === 'Calendar' && <CalendarPage key={dataVersion} />}
        {page === 'Strategies' && <StrategiesPage key={dataVersion} />}
        {page === 'Trades' && <TradesPage key={`${dataVersion}-${mode}`} mode={mode} />}
        {page === 'Executions' && <ExecutionsPage key={dataVersion} />}
        {/* A cash edit moves the equity curve, so the other pages remount
            when one lands. Cash itself must not, for the same reason
            Import does not: it would discard the form it is standing in. */}
        {page === 'Cash' && <CashPage onChanged={() => setDataVersion((v) => v + 1)} />}
        {page === 'Journal' && <JournalPage onChanged={() => setDataVersion((v) => v + 1)} />}
        {page === 'Expiry Watch' && <ExpiryWatchPage key={dataVersion} />}
      </main>
    </div>
  )
}
