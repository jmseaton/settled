# Settled — Trade Performance Tracker

[![Tests](https://github.com/jmseaton/settled/actions/workflows/test.yml/badge.svg)](https://github.com/jmseaton/settled/actions/workflows/test.yml)

Single-user, self-hosted performance tracking for a personal Interactive Brokers account.

**It keeps itself current.** A scheduled daily pull from IBKR's Flex Web Service is the
primary data path: each run fetches a trailing 30-day statement — executions, IB's own
closed lots, open-position marks, cash transactions, transfers and `ChangeInNAV` — then
reconstructs round-trip trades from raw executions by FIFO lot matching, reconciles the
result against the broker's own position *and* realized-P&L records, and recomputes every
account- and trade-level statistic. File upload (`.tlg` TradeLog or Activity Flex XML)
remains as backfill and as the way to run the app without credentials; both paths converge
on one pipeline the moment the bytes are in hand.

Built to [`docs/trade-performance-tracker-spec.md`](docs/trade-performance-tracker-spec.md).
Section references throughout the code (`§5.2`, `§14.1`, …) point into that spec.

> **Not a tax record.** FIFO matching serves performance attribution only. There is no
> wash-sale handling, no tax-lot election, and no short/long-term split.

## What is built

**Phases 1, 1b, 2, 3, and 4** of the spec's milestones (§15), grouped by where each piece
sits in the path from broker to chart:

**Getting the data in**

| Area | Status |
|---|---|
| **Flex Web Service client** — two-step protocol, classified retries, token redaction (§3.7–3.8) | ✅ |
| **Sync runner, daily scheduler, run history, staleness guard** (§3.8) | ✅ |
| **Flex XML parser** — Trades, CLOSED_LOT, SecurityInfo, OpenPositions, Transfers, CashTransactions, ChangeInNAV (§3.9) | ✅ |
| `.tlg` parser, all record types and section layouts (§2) | ✅ |
| Import pipeline — parser registry, checksum, idempotent upsert, import report (§3.1–3.3) | ✅ |
| **Original payloads archived by SHA-256** (§3.2) | ✅ |
| **Cash transactions — Flex ingestion, type mapping, manual CRUD and CSV paste (§4.7, §7.1)** | ✅ |
| **NYSE market calendar** — trading-day DTE and scheduler skips (§9.5, §3.8) | ✅ |
| **Read-only Flex diagnostics probe** (`python -m app.flex.diagnose`) | ✅ |

**Trades, and checking them against the broker**

| Area | Status |
|---|---|
| Reconciliation against the broker position snapshot (§3.4) | ✅ |
| **CLOSED_LOT reconciliation against IB's own `fifoPnlRealized` (§3.4)** | ✅ |
| Trade construction — FIFO, position flips, expirations (§5.1–5.3) | ✅ |
| **Transferred positions — synthetic opening legs, both basis policies (§5.5)** | ✅ |
| **Assignment and exercise — detection, linking, chains, the banner (§5.4)** | ✅ |
| **Spread grouping, classification, manual overrides** (§6.1–6.4) | ✅ |
| **Analyze by Leg / Analyze by Strategy toggle** (§6.4) | ✅ |
| Books — `Holdings` vs `Options Income` (§6.5) | ✅ |

**Performance**

| Area | Status |
|---|---|
| **Performance epoch — configurable, IBKR cross-check, full rebuild on change (§0.3)** | ✅ |
| **Account equity curve with selectable variants (§7.2, §7.4)** | ✅ |
| **TWR, XIRR, CAGR and simple return (§7.3)** | ✅ |
| **SPY benchmark — cash-flow-matched shadow portfolio, alpha/beta/IR (§7.5)** | ✅ |
| **`PriceSource` adapters (Tiingo, Stooq) and the `price_bars` cache (§4.10)** | ✅ |
| **Unrealized P&L and true account value, from Flex marks (§12)** | ✅ |
| Statistics — P&L, ratios, sample-size gating (§8.1–8.2) | ✅ |
| **Risk ratios on account returns as well as trade P&L (§8.2)** | ✅ |

**Interface**

| Area | Status |
|---|---|
| **Chart grid — any metric × any dimension, with drill-down (§9.1–9.2)** | ✅ |
| **Global filters and the monthly P&L calendar heatmap (§9.3)** | ✅ |
| **Trend chart, regime markers, distribution histogram (§9.4, §6.6)** | ✅ |
| **Curatable dashboard, and Sharpe reported with its confidence interval (§14.6)** | ✅ |
| Cumulative net P&L chart (§9.3 #1) | ✅ |
| Expiry Watch (§9.5) | ✅ |
| Executions and trades grids (§10) | ✅ |
| **Tags, notes, bulk operations (§4.8, §4.9, §10.3, §11.1–11.2)** | ✅ |
| **Stop/target with R-value, manual trade entry, splits (§11.3)** | ✅ |

**Deliberately not built yet**, and why it matters when reading the numbers:

- **Intraday market data (§12, Phase 5).** Daily marks arrived with Phase 4, but MFE/MAE,
  best exit, exit efficiency and running P&L all need 1-minute OHLC over each holding
  period. Options intraday history is the expensive, hard part; the Phase 4 placeholder
  columns on `trades` stay null rather than approximate.
- **Rebasing a straddling trade's opening basis** to the epoch-date mark (§0.3). That needs
  an epoch-dated position snapshot, which only exists once the app has been syncing since
  the epoch. Until then those trades are flagged `straddles_epoch` and dropped from duration
  statistics rather than silently carrying a pre-epoch basis.
- **Assignment against real data.** Every §5.4 path is implemented and tested, but the
  account has never actually had an assignment — §16.2's tests run against synthetic
  statements, as the spec asks. This is §14.6's one item code cannot close; the draft note
  an assignment creates now carries the conventions to check, so the re-read arrives with
  the event rather than depending on memory.
- **The remaining §9.4 views** — treemap, streak chart, scatter compare. Drawdown,
  distribution and trend are built.
- **The token-expiry countdown (§0.6).** An expired token is classified, never retried, and
  raises the sync banner within a day — but that is detection after the fact. The T-30 and
  T-7 warnings the spec asks for need `token_issued_at` / `token_expires_at` in config, and
  those settings do not exist yet, so the expiry date still has to live somewhere a human
  looks.

## How data gets in

Two paths, one pipeline. `parse → validate → stage → normalize instruments → idempotent
upsert of executions → rebuild trades → recompute metrics → reconcile → import report`
(§3.1–3.3) runs identically whichever way the bytes arrived; a parser registry keyed on
`source_format` is the only branch, so a third format would be a new adapter rather than a
new pipeline.

**The sync is the path that matters.** With `SETTLED_FLEX_TOKEN` and `SETTLED_FLEX_QUERY_ID`
set, an in-process scheduler runs the two-step Flex protocol at **05:00 ET** every trading
day — after IB's overnight settlement and P&L processing, which is why several of §3.8's
transient error codes exist at all.

- **A trailing 30-day window on every run, not "since last sync".** Trades get amended and
  same-day busts corrected, and a since-last-sync window would never see them again. The
  overlap costs nothing because executions upsert on the broker's own identifiers.
- **Days the market was closed are skipped**, against the NYSE calendar rather than a
  weekday test. Nothing is lost by skipping: Monday's window covers the weekend, including
  Sunday-evening futures sessions.
- **Every attempt leaves a row in `flex_sync_runs`, failures included**, and errors are
  classified: transient ones retry, credential and configuration ones never do and raise an
  alert instead, because an expired token needs a human rather than a fourth attempt.
- **The banner reads from the last *success*, not the last attempt** (`/api/sync/health`),
  and distinguishes "no scheduler configured" from "scheduled and healthy" from "failing and
  needs you". See the staleness note under [Conventions](#conventions-worth-knowing).
- **One Postgres advisory lock per run**, so a second worker or a second container steps
  aside rather than syncing concurrently against a token limited to one request per second.
- **The token never reaches the database** (§3.10). Environment only, and redacted from
  every log line and every stored error message: it grants read access to the full statement
  history and travels as a query parameter.
- **A sync also refreshes the benchmark price bars** (§7.5). A dead price API marks that line
  stale; it does not fail the run, because a price outage is not a reason to lose a day of
  trade data.

`POST /api/sync/run` does the same work on demand, under the same lock and recorded with
trigger `MANUAL`. It takes no date parameters — there is no path that forces a statement for
a past date. When a sync fails opaquely, `python -m app.flex.diagnose` probes the service
read-only and prints a plain request beside a date-overridden one; that is the one
distinction the app cannot make from the inside, because every request it sends overrides
the dates.

**Upload is the fallback, and it is a genuine subset.** The Import page accepts either
format and detects which from the content, and re-importing an overlapping window is safe.
But a `.tlg` carries only executions and position lots. It has no `transactionID` or
`brokerageOrderID` (so combo grouping falls back to exact-timestamp matching, §6.1), no
`CLOSED_LOT` records (so the broker P&L check below cannot run), and no cash transactions,
transfers, position marks or `ChangeInNAV` — so §7's account-level machinery has nothing to
work from beyond what you type in by hand (§7.1). Everything in the next section, the
reconciliation to the cent included, comes off the Flex path.

**A missed morning is a permanent hole.** Trades backfill; marks do not. Each statement
reports open positions as of its `period_end`, so one run contributes exactly one day of
marks however wide its window, and no later run can fill a day the scheduler missed — see
[Deployment](#deployment) for why that makes uptime a real requirement rather than a nicety.

## Account-level performance

The equity curve is anchored on the **performance epoch** (§0.3) — a user-chosen start date
and the account value on it — not on account opening. Early experimentation would otherwise
contaminate every statistic with no way to un-mix it later. Without both settings, every
percentage measure is **withheld with a stated reason** rather than computed against an
assumed zero: a plausible-looking wrong percentage is the specific failure this app exists
to prevent. The value can be fetched from IBKR rather than typed, and IBKR's own figure is
kept beside the stored one so divergence stays visible.

**The curve reconciles against IBKR to the cent.** For the fixture window:

| | |
|---|---|
| Epoch value (IBKR `ChangeInNAV.startingValue`) | 389.15029091478 |
| + net external flows (deposits 969.65 · ACATS transfer 2,598.28) | 3,567.93 |
| + realized P&L incl. partial closes | 329.79 |
| + dividends, interest and fees | −8.84 |
| + unrealized on the open book, from Flex marks (§12) | 72.83 |
| = **account value, `WITH_INCOME`** | **4,350.86** |
| = **IBKR `ChangeInNAV.endingValue`** | **4,350.85747501788** ✅ |

Every link in that chain has to be right for it to close — the epoch anchor, the deposit
mapping, the transfer valued at its transfer-date mark, realized P&L including partial
closes, fees, and the mark on the open book. Getting deposits right and transfers wrong
would still balance the account; this would not. It is asserted as a test.

Phase 3 could only close this by adding IBKR's own unrealized figure back by hand. Phase 4
found the marks were already in the data: Flex publishes `markPrice` and
`fifoPnlUnrealized` on every open position, and they sum to exactly
`ChangeInNAV.changeInUnrealized`. TWR moved from 8.52% to **10.12%** against IBKR's reported
10.2478%. The residual is expected and stated: one statement cannot reconstruct the daily
marked path IBKR chains its figure from, and it converges as sync history accumulates.

Marks are only as dense as the import history, so `unrealized_priced` records which days
carry a real one and a stale mark is **never carried forward** — holding one flat across a
week asserts a week of zero market movement, which the data does not support.

Two corrections that check forced, both worth knowing about when reading older numbers:

- **Realized P&L from partially closed positions** never reached the equity curve. §8.1
  separates "net closed P&L" from "realized incl. partials", and the fixture was understated
  by $1.70 — small, permanent, and growing. It now lives in its own column, so account value
  is right while the win rate stays honest about which positions actually have an outcome.
- **`daily_snapshots` covered only days something closed.** A series built that way
  compresses the x-axis and hands Sharpe a return stream with all its zeroes deleted. Rows
  now exist for every trading day in the measured period, which also widens the sample the
  ≥30-day ratio gate counts.

### The benchmark

Cash-flow matched, per §7.5: the same money, on the same dates, passively invested in SPY at
the adjusted close, with fractional shares never rounded. Comparing against the index's raw
period return would be apples-to-oranges, because the index has no deposits in it.

Returns on both sides are **time-weighted with flows netted out**, and that detail is
load-bearing. On the day the $6,042 ACATS transfer lands, the account and its shadow both
jump by $6,042 — the account because the cash arrived, the shadow because it bought SPY with
that same cash. Regressing raw value changes finds those jumps in lockstep and reports beta
1.00 with correlation 1.00: a perfect fit to the deposit schedule, measuring nothing. For the
same reason the headline is the **dollar gap** between the two curves rather than a
start-to-end multiple, which on this account would read "1001% return" and be a true
statement about funding rather than about trading.

Alpha, beta, correlation, tracking error and the information ratio all sit behind the same
≥30-trading-day gate as §8.2, and the UI prints "insufficient data" rather than a
confident-looking beta.

Prices are read **only** from `price_bars`; no calculation calls a provider. Adapters exist
for Tiingo (recommended — `adjClose` and `divCash` are separate fields, so the total-return
question is unambiguous) and keyless Stooq. Stooq's dividend treatment is undocumented, so
its adapter reports `adj_close` as `close` and carries the price-only caveat in its payload
rather than labelling a price series as adjusted. A provider outage degrades to the last
cached bar, marks the line stale, and does not fail the sync run.

```bash
export SETTLED_TIINGO_API_KEY=...   # optional; falls back to keyless Stooq
export SETTLED_PRICE_SOURCE=auto    # auto | tiingo | stooq
```

## The broker P&L check

The strongest correctness guarantee in the app, and the reason the Flex path matters. A
position snapshot proves the *ending state* is right; IB's `CLOSED_LOT` records prove the
*path* was right, by publishing its own FIFO matching with a realized P&L per lot.

Against `fixture-flex.xml`, all **35 comparable trades match IBKR to within $0.01, with an
aggregate difference of exactly $0.0000** across 40 closed lots.

Two divergences are carved out explicitly rather than absorbed by a loosened tolerance,
because a widened tolerance would hide real bugs everywhere else:

- **Transferred positions under `TRANSFER_MARK`** (§5.5). IB values BCGX at the delivering
  broker's acquisition cost and reports **+66.33**; under the default policy this app values
  it at the transfer-date mark and reports **−41.56**. Switching to `ORIGINAL_COST` makes the
  two agree exactly — 36/36 trades, difference $0.0000 — which is the check for that policy.
- **Assignment chains** (§5.4). IBKR rolls option premium into the underlying's basis; this
  app keeps them separate so option performance stays visible. Not yet reachable.

Note the blind spot this creates: under `TRANSFER_MARK` a transferred trade is not compared
against IB at all, so an error in *its* P&L is invisible to the check. That is asserted as a
test rather than left implicit.

## What a Sharpe ratio is actually worth here

§14.6 left the ≥30-trading-day gate open, calling it *"a reasonable default, not a
researched one"*. It is now derived rather than assumed.

Every Sharpe is reported with the standard error and 95% interval from Lo (2002),
`SE(SR) = sqrt((1 + SR²/2) / n)`, alongside the sample size that Sharpe would need to be
distinguishable from zero, `n ≥ z²(1 + SR²/2) / SR²`.

The result is worth stating plainly, because it is not flattering:

> This account's annualized Sharpe of **3.91 over 62 observations has a 95% interval of
> [−0.10, 7.92]**. It includes zero. A more ordinary Sharpe of 1.0 would need roughly 970
> trading days — about four years — before it cleared zero at 95%.

So ≥30 days was far too permissive for the ratios it gated, and the fix is not a bigger
number. The sample a Sharpe needs depends on the Sharpe, so no fixed threshold can be right
in general. The cutoff survives as a configurable *display* preference
(`SETTLED_MIN_SAMPLE_TRADING_DAYS`) that decides only whether to draw a figure; the interval
decides whether to believe it, and the UI draws the banner in warning colours whenever the
interval straddles zero.

Lo's formula assumes IID returns. Trading returns are autocorrelated and fat-tailed, both of
which make the true interval *wider*, so what is reported is a floor on the uncertainty
rather than a bound — the safe direction to be wrong in, and the payload says so.

## Regime changes (§6.6)

Statistics pooled across a change in how you trade describe a portfolio that no longer
exists. The fixture shows it: MES was vertical spreads through 2031-07-06 and naked short
puts from 2031-07-13, and a single "MES win rate" spans both.

Three mechanisms, per §6.6, and the app deliberately implements none of them as automatic
detection — that is over-engineering for one user, and a marker you wrote records *why*,
which no detector can:

- `strategy_type` filters at **group** level through the chart grid's global filters.
- The **trend chart** (§9.4) plots any metric per *trade* rather than per date, with SMA and
  EMA overlays. The axis is the point: calendar spacing reflects elapsed time, trade spacing
  reflects decisions made, and a change in behaviour only shows on the second.
- **Regime markers** are dated annotations drawn as vertical lines, placed at the first
  trade closing on or after the date. One dated after the last trade is listed rather than
  drawn, since there is nothing yet for it to mark.

## The dashboard is a starting point, not a fixture

§9.3's default twelve were an admitted guess (§14.6). Rather than replace one guess with
another, the set ships as an editable layout — add, remove, reorder, persisted. Every panel
is a `(dimension, metric)` pair over the §9 grid, so curation is a saved selection over ~266
possible views rather than a rewrite.

## Assignment (§5.4)

The exposure §5.4 is written around is naked short futures options: a defined-risk vertical
caps an assignment, a naked short MES put does not — it becomes a long futures position of
roughly $35,600 notional per contract. So the handling exists before it fires rather than
being retrofitted during a live one.

**The broker is the only authority.** In-the-money status settles on a 3 p.m. CT price
fixing this app cannot observe, so nothing here infers an outcome; it reports what settled.
The `OptionEAE` section is the authoritative source and is read alongside the `A`/`Ex`/`Ep`
flags, because the flag travels with the execution while the section can lag by a statement.

**P&L uses convention (a)** — the option closes at 0 with its full premium realized, and the
underlying opens at the strike. IBKR uses (b) and rolls the premium into the stock's basis,
which makes the premium invisible to every option statistic in §8. The two agree at the
*chain* level, and that is checked explicitly (test 40) rather than by loosening the
per-trade tolerance, which would hide real bugs everywhere else.

**A chain is a second view, not another strategy.** It spans trades that already belong to
their own spreads, so every aggregate excludes it — the statistics totals, the chart grid,
the §6.3 risk segments and the trades grid. Counting it alongside would put the option leg's
P&L in the totals twice. It has its own section on the Strategies page, where the two
figures a pooled segment cannot express live: risk frozen at its pre-assignment value, and
the capital the resulting position actually tied up, reported side by side and never
averaged. Durations are split for the same reason.

**The banner covers assignments and exercises only.** Expirations were on the calendar and
Expiry Watch forecast them; bannering the fixture's ten would train you to dismiss it on
sight. Dismissal survives resync.

## Requirements

- Python 3.11+
- PostgreSQL 16
- Node 20+

## Setup

This is the development loop — two processes and a local Postgres. To actually *run*
the thing daily, see [Deployment](#deployment) below; the distinction matters more than
it usually does, because the daily sync is the only source of the mark history behind
unrealized P&L.

```bash
# Database
sudo -u postgres psql -c "CREATE USER settled WITH PASSWORD 'settled_dev' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE settled OWNER settled;"
sudo -u postgres psql -c "CREATE DATABASE settled_test OWNER settled;"

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.schema_setup          # create tables
uvicorn app.main:app --port 8000

# Frontend (separate shell)
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

Override the connection string with `SETTLED_DATABASE_URL` if your setup differs, and the
payload archive location with `SETTLED_DATA_DIR` (default `./data`, gitignored).

### Flex credentials — the primary data path

Set these and the app feeds itself; leave them unset and it is an upload-only install — no
position marks, no `CLOSED_LOT` cross-check, and account-level return only as far as
hand-entered cash carries it (see [How data gets in](#how-data-gets-in)). Credentials come
from the environment and are never written to the database (§3.10) — the token grants read
access to your full statement history and travels as a query parameter:

```bash
export SETTLED_FLEX_TOKEN=...      # Client Portal -> Flex Web Service Configuration
export SETTLED_FLEX_QUERY_ID=...   # the Activity Flex Query id from §3.6
```

With those set, the app schedules a sync for **05:00 ET daily** and skips days the market
was closed. `POST /api/sync/run` triggers one on demand, and `python -m app.flex.diagnose`
probes the service read-only when a run fails opaquely. Without them, no scheduler starts at
all — an upload-only deployment has no job failing every morning.

Set `SETTLED_SCHEDULER_ENABLED=false` to run the app with credentials but drive sync
externally (a host cron, say). Each run takes a Postgres advisory lock, so a second worker
or a second container steps aside rather than syncing concurrently against a token limited
to one request per second.

The token expires — one year at most (§0.6), and generating a new one invalidates the
current one, so rotation is deliberate rather than automatic. Error `1012` is never retried
and alerts in the same class as a credential failure.

## Deployment

Docker Compose, three containers — Postgres, the API with its scheduler, and nginx
serving the built frontend. Full runbook in [docs/deployment.md](docs/deployment.md).

```bash
cp .env.example .env
$EDITOR .env                 # POSTGRES_PASSWORD required; Flex credentials optional
docker compose up -d --build
```

Then `http://<host>:8080`. **No authentication sits in front of it** (§1.3 — single user,
local/LAN), so put it somewhere you trust or set `SETTLED_BIND=127.0.0.1` and reach it
over SSH.

Two things about this deployment are worth knowing before you rely on it.

**Uptime at 05:00 ET is a real requirement, not a nicety.** Trades backfill — the sync
window is a trailing 30 days, so a missed morning costs nothing there. Marks do not. Each
Flex statement reports open positions as of its `period_end`, so one run contributes
exactly one day of marks however wide its window, and a morning the scheduler doesn't fire
is a permanent hole in every open position's mark path. That path is the unrealized-P&L
series, and it is the only available source of MFE/MAE for options (§12): options intraday
history is expensive and no free provider serves futures options at all. A box that sleeps
is a box with a gappy mark history, and the gaps cannot be filled in later.

**`create_all` does not alter existing tables.** Alembic is the migration tool of record
(§13) but there is no migration history yet, so an upgrade that adds a column finds every
table already present and changes nothing. The entrypoint checks for exactly this on every
start and prints what is missing rather than letting it surface as an `UndefinedColumn`
error three pages later. It reports rather than repairs — see the runbook for how to
recreate and re-import, and back up first, because manual entries (tags, notes, journal,
stops, regime markers, manual basis) live only in the database.

## Tests

```bash
cd backend && source .venv/bin/activate
python -m pytest tests/ -q
```

421 tests, keyed by the spec's own §16 test numbers where they apply:

| File | Covers |
|---|---|
| `test_acceptance_tlg.py` | §16 tests 1–16b, 62–63, 65–74 against both `.tlg` fixtures |
| `test_benchmark.py` | §16.6 tests 50–55, the price adapters, and the flow-netting fix |
| `test_performance.py` | §0.3 epoch behaviour, §7.2–7.4 curve variants, the NAV reconciliation |
| `test_returns.py` | §7.3 TWR, XIRR, CAGR and simple return as pure functions |
| `test_cash_transactions.py` | §7.1 type mapping, Flex restatement, manual rows, CSV paste |
| `test_charts.py` | §9 the full dimension × metric cross product, filters, calendar |
| `test_assignment.py` | §16.2 tests 40–49 against synthetic statements, plus the no-double-counting invariant |
| `test_journal.py` | §11 tags, notes, bulk ops, stop/target, manual entry, splits |
| `test_open_items.py` | §14.6 and §6.6 — Sharpe significance, trend chart, regime markers, dashboard curation |
| `test_flex_import.py` | §16.1 tests 26–28 and §16.3 transfer handling |
| `test_flex_client.py` | §16.1 tests 17–25, mocked HTTP, no live calls |
| `test_flex_sync.py` | §3.8 runner, run history, and §16.1 test 24 staleness |
| `test_flex_diagnostics.py` | what the app says when IBKR answers with an HTML error page, a CSV query or an empty body — and that the token never appears in the message |
| `test_grouping.py` | §16 tests 12–14 and §16.4 tests 56–63 |
| `test_scheduler.py` | §3.8 schedule, trading-day guard, advisory lock |
| `test_market_calendar.py` | NYSE holidays against the published calendar |
| `test_gap_closure.py` | §6.4 analysis mode and manual overrides, §3.2 payload storage |
| `test_schema_check.py` | the deployment drift detector — missing columns and tables, and what is deliberately not drift |
| `test_trade_construction.py` | FIFO paths the fixtures don't reach (partial closes, flips, DST) |
| `test_stats_engine.py`, `test_parser.py`, `test_api.py` | unit and endpoint coverage |

Asserted figures include the BCGX round trip (**+257.02**), leg-level ACME
(**−61.37** / **+103.75**), the group results those legs actually belong to
(**+42.37**, **+20.90**, **+1.90**), file totals
(**−749.65** / **−22.11**), cross-format equivalence (**1,549.56** / **−21.62** from both
the `.tlg` and the XML), the CLOSED_LOT reconciliation above, and the §7 cash figures
checked against `ChangeInNAV` (deposits **969.65**, transfers **2,598.28**, other fees
**−8.84**, ending value **4,350.86**).

Tests for assignment (§16.2) are not stubbed — those code paths aren't reachable yet, and an
empty passing test is worse than an absent one.

Two Phase 3 tests deliberately assert behaviour that looks like a bug and is not, so that a
later reader does not "fix" it:

- **A mid-period deposit lowers TWR.** Undeployed cash genuinely dilutes every subsequent
  period's percentage return. What must not move is the deposit's *own* contribution, and
  that is what the test pins — cumulative TWR through a flat day is unchanged by dropping
  $5,000 into it.
- **Beta is withheld when the benchmark never moves.** A flat series has no variance to
  regress against, so `None` is the answer; returning a number would be dividing by noise.

Tests run against `settled_test` and drop/recreate the schema per test. Point them elsewhere
with `SETTLED_DATABASE_URL`.

### Continuous integration

`.github/workflows/test.yml` runs on every pull request and every push to `main`:

| Job | What it does |
|---|---|
| **Backend** | The full suite against a `postgres:16` service container, on Python 3.11 and 3.12 |
| **Frontend** | `npm ci`, `tsc --noEmit`, production build |
| **Fixtures still anonymized** | `scripts/check_fixtures_scrubbed.py` |

The fixtures job exists because it is the one check that cannot be usefully run late.
`fixtures/README.md` puts it plainly — *git history is permanent, anonymize before the first
commit, not after* — so a fixture regenerated from IBKR and committed without re-running the
substitutions is a mistake no later fix can undo. The script verifies the account IDs, the
`.tlg` `ACT_INF` holder name and mailing address, and the Flex `accountId`,
`Transfer/@account`, and `Transfer/@deliveringBroker` attributes, and rejects any date early
enough to have come straight from the broker. It is a guard against forgetting, not a
privacy proof.

## Fixtures

[`fixtures/`](fixtures/) holds three anonymized broker exports and their provenance notes.
They keep every structural quirk of real IBKR output while carrying none of the account
behind it: the identifiers are scrubbed, the instruments are fictional, the dates are
shifted, and every monetary value is scaled by a constant — uniformly, so the relationships
the tests assert on still hold exactly. `fixtures/README.md` explains both passes and what
each costs.

`fixture-orphan.tlg` is the realistic case: BCGX was transferred in by ACATS, so no opening
execution exists in the account and the close is an orphan. `fixture-complete.tlg` adds a
**hand-reconstructed** BCGX opening — see the caution in `fixtures/README.md`; it is valid
for round-trip mechanics only, never as a source of truth for BCGX P&L.

## Layout

```
backend/app/
  flex/           Web Service client, error classification, sync runner,
                  scheduler, diagnostics probe (§3.7-3.8)
  parsers/        .tlg and Flex XML parsing, symbol decoding (§2, §3.9)
  importer/       pipeline, instrument normalization, cash, closed lots,
                  option events, corporate actions, both reconciliations (§3)
  trades/         FIFO trade construction, transfer basis, grouping,
                  assignment chains (§5, §6)
  stats/          daily snapshots, the pure-function metric engine, Sharpe
                  significance (§8, §14.6)
  performance/    epoch, return functions, equity curve, SPY benchmark (§0.3, §7)
  prices/         PriceSource adapters (Tiingo, Stooq) and the bar cache (§4.10)
  charts/         the dimension × metric grid, filters, calendar, trend,
                  dashboard layout (§9)
  cash/           manual cash-transaction entry and CSV paste (§7.1)
  journal/        tags, notes, stops/targets, manual entries, re-anchoring (§11)
  expiry_watch/   forward-looking expiry projections (§9.5)
  contracts/      static MES/ES contract reference (Appendix D)
  models/         SQLAlchemy models, including flex_sync_runs (§4)
  market_calendar.py  NYSE trading days, computed rather than hardcoded
  schema_check.py     the deployment schema-drift detector (§13)
  api/            FastAPI routes
frontend/src/
  SyncBanner.tsx  §3.8 staleness and credential-failure banner
  pages/          Import, Dashboard, Performance, Statistics, Charts, Trend,
                  Calendar, Strategies, Trades, Executions, Cash, Journal,
                  Expiry Watch
```

Raw executions are never mutated; trades, snapshots, and every statistic are rebuilt from
them on each sync or upload (§3.2, §13). That is what makes the matching algorithm safe to
change — and it is why a daily sync costs nothing to re-run over a window it has already
seen.

## Conventions worth knowing

- **`proceeds` is signed cost** in the TradeLog convention — a sell is negative. Cash flow
  is `−proceeds + commission`, with commission already negative (§2.2). Flex's field of the
  same name is the *negation*, and the two parsers accordingly share no arithmetic — each
  normalizes at its own boundary (§3.9).
- **Trading day ≠ calendar day for futures.** A futures or futures-option fill at or after
  18:00 ET belongs to the next session (§14.1). Derived once, at import, and stored on the
  execution; every `GROUP BY` uses it.
- **Timestamps are stored tz-aware and served in `America/New_York`.** Postgres returns
  `timestamptz` in UTC, so the API converts explicitly — otherwise an 18:07 ET futures fill
  displays as 22:07 and disagrees with the broker statement.
- **P&L is realized only.** An open or partially closed trade reports the result of the
  portion actually closed, never `−Σproceeds`, which is the open cost basis in disguise.
- **Totals are mode-invariant; per-unit figures are not.** The sum of legs equals the sum of
  groups, so net P&L never moves when you flip the analysis toggle. Win rate does — 80.0% by
  strategy against 52.9% by leg on the fixture — because a vertical is one outcome as a
  strategy and one win plus one loss as legs. Both are true; only one answers "how good were
  my decisions" (§6.4). The default is by strategy.
- **A manual grouping is keyed on the broker's execution identifiers**, never on trade ids:
  trades are fully re-derived on every import and none of their ids survive, so a
  trade-id-keyed override would silently vanish on the next sync (§6.4).
- **Archived payloads never enter source control.** `data/` is gitignored: those are real
  account statements, and the fixtures README's warning about permanent git history applies
  with more force to live data than to scrubbed samples.
- **A spread's legs are not a spread.** Per-leg P&L on a vertical is individually
  meaningless, so grouping is the analytical unit (§6). Combos are detected by the broker's
  `brokerageOrderID` where present, falling back to an *exact* timestamp match — a same-day
  match would fabricate spreads from unrelated positions, so it is deliberately strict. How
  each group was formed travels with it as `detection_source`.
- **`max_risk = null` means unbounded, not unknown.** A naked short call has no ceiling. It
  is never substituted with a finite placeholder and never dropped from a denominator; both
  would inflate exactly the average it should have dragged down. Return on risk is also
  never pooled across strategy types (§6.3) — the API returns segments and an explicit
  refusal rather than one plausible-looking blended number.
- **A trading day is not a weekday.** DTE and sync staleness both count against the NYSE
  calendar (`app/market_calendar.py`), so a holiday neither inflates a hold time nor trips a
  false staleness alert. The rules are computed, not hardcoded, and the module refuses to
  answer for pre-2022 years rather than applying today's holiday set to an era before
  Juneteenth existed.
- **Sync staleness is measured from the last *success*, not the last attempt.** The failure
  §3.8 exists to prevent is a token expiring while nightly runs keep failing and the
  dashboard keeps showing plausible stale numbers. Failed attempts are recorded precisely
  because they are the ones that explain a stale dashboard.
- **An orphan close creates no position.** A close with no opening does not open a short in
  the opposite direction. If a Transfers row or a closed lot supplies a basis, the opening
  leg is synthesized (`synthetic = true`, never shown as a real fill) and the trade becomes
  `TRANSFERRED`; otherwise it stays an orphan with no P&L attributed and a warning (§5.5).
- **The transfer basis policy changes what P&L means**, so it travels with the data: the
  active policy is returned alongside every trade list and displayed above the grid (§14.3).
  Changing it re-derives every trade from raw executions.
- **The `.tlg` and Flex parsers share no arithmetic.** Both formats ship a field called
  `proceeds` and they are negatives of each other; each parser normalizes at its own
  boundary and everything downstream works from the canonical form (§3.9).
