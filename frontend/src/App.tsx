import { useEffect, useState } from 'react'
import { api } from './api'
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

  useEffect(() => {
    api.settings().then((s) => setMode(s.analysis_mode)).catch(() => undefined)
  }, [])

  async function changeMode(next: string) {
    setMode(next)
    try {
      await api.setAnalysisMode(next)
    } finally {
      setDataVersion((v) => v + 1)
    }
  }

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">
          Settled
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
      </nav>

      <main className="main">
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
