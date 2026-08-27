# Trade Performance Tracker — Build Specification

**Version:** 4.0
**Status:** Draft for implementation
**Owner:** Jared
**Target:** Single-user, self-hosted web app for evaluating personal trading performance
**Data source:** Interactive Brokers — automated Flex Web Service sync (primary), TradeLog `.tlg` / Flex XML upload (fallback)
**Asset classes:** Stocks/ETFs, stock options, futures, futures options

---

## 0. Before You Start

Six things to do before writing code. The first two are the ones that will cost real rework if skipped.

### 0.1 Generate a real Flex XML and diff it against the `.tlg` — ✅ **DONE**

Completed against a 90-day Activity Flex XML (2031-05-12 to 2031-08-08). Full results in **Appendix C**. Six spec corrections resulted; all are applied. The remaining items below are updated to reflect what that file answered.

1. Create the Activity Flex Query per §3.9 and enable the Flex Web Service (§3.6).
2. Pull XML for **2031-06-02 to 2031-08-08** — the same window as the fixture.
3. Compare, by hand, before writing a parser:
   - Do all 79 executions appear, with matching quantities and prices?
   - Is Flex `proceeds` the opposite sign to `.tlg` `proceeds` (§3.9)? Confirm on one buy and one sell.
   - Do the section and field names match §3.9's list? Correct the spec where they don't.
   - Does `Financial Instrument Information` carry usable metadata for the MES futures options — the ones whose local symbols (`EX1Q6 P7120`) are unparseable?
   - Does `CashTransactions` actually return anything for this period?
   - Does `Transfers` contain the BCGX ACATS record?

This is acceptance test 26 run manually, before there is code to run it against. An afternoon here saves rewriting a parser later.

### 0.2 Anonymize the fixtures — ✅ **DONE**

Three fixtures, not two — the Flex XML is also a fixture and also carried identifiers.

Two passes. The first removes the account's identity; the second removes its *linkage*,
which is the one people forget. See `fixtures/README.md` for both in full.

| File | What was scrubbed |
|---|---|
| `fixture-complete.tlg` | `ACT_INF`: account ID, full legal name, mailing address |
| `fixture-orphan.tlg` | Same |
| `fixture-flex.xml` | `accountId` throughout; `Transfer/@account` (the delivering broker's account number); `Transfer/@deliveringBroker`; the masked account reference in `CashTransaction/@description` |

The Flex XML contains **no** name or address — IBKR's `AccountInformation` element omits both. Only account numbers needed replacing.

The identity scrub changed no value. The de-linking pass did: it replaced every instrument
with a fictional one, shifted every date by a constant, and scaled every monetary value by a
single constant. Because that scaling is uniform and linear, every relationship the
acceptance tests assert on survives exactly — and anything ratio-shaped is untouched, which
is why TWR is still 10.24784403. Structure is likewise unchanged: 78 executions, 22 combo
groups, `fifoPnlRealized` 437.68, commissions −21.62.

Re-run **both** passes on every regeneration. Git history is permanent — do it before the
first commit, not after.

### 0.3 Performance epoch — ✅ **DECIDED: user-configurable**

The account opened 2030-11-06, but performance should be measured from **when the current strategy started**, not from account funding. Early experimentation would otherwise contaminate every statistic, and there is no way to un-mix it later.

Two settings, both editable:

| Setting | Notes |
|---|---|
| `tracking_start_date` | User-chosen. Independent of `dateOpened`. |
| `tracking_start_value` | Account value on that date. |

**Fetch the value rather than typing it.** A Flex request with the date override (§3.7) — `&fd=YYYYMMDD&td=YYYYMMDD` — returns `ChangeInNAV.startingValue` for exactly that date. Offer a "fetch from IBKR" button next to the field, prefill it, and let the user override. Typing a NAV by hand is an error vector that silently skews every percentage in the app.

**Cross-check, always visible.** Display IBKR's figure beside the stored one and flag any divergence. A wrong starting value produces returns that look plausible and are wrong — exactly the failure this app is meant to prevent.

Behaviour around the epoch, all of which reuses existing machinery:

- **Import everything, measure from the epoch.** Pre-epoch executions are still ingested — they supply the cost basis of positions open on day one — but are excluded from every statistic in §8 and from the equity curve. Do not skip importing them; the data is not re-fetchable indefinitely.
- **Positions open at the epoch** are rebased to their epoch-date mark. This is the same problem as a transferred-in position, so use the same `TRANSFER_MARK` logic (§5.5) with `origin = PRE_EPOCH`.
- **Trades straddling the epoch** (opened before, closed after) are included with a rebased opening basis, flagged `straddles_epoch`, and **excluded from duration statistics** — a hold time that began before you were measuring is not a hold time you can learn from.
- **Changing the epoch is a full rebuild**, not an incremental update. Warn on change and re-derive `daily_snapshots`, all trades, and the benchmark shadow portfolio (§7.5) from raw executions. Cheap at this data volume, and it keeps the setting genuinely reversible.
- **The benchmark shadow portfolio starts at the epoch too**, seeded with `tracking_start_value` as its initial flow. Otherwise you are comparing different periods.

Still to confirm: that a query reaching back to the chosen date returns complete cash history. The 90-day test only proves the sections populate.

### 0.4 Verify combo grouping — ✅ **DONE, and the answer was not `ibOrderID`**

`ibOrderID` is **per leg** and does not group combos. **`brokerageOrderID` does** — 22 correctly paired verticals, zero false pairings. §6.1 rewritten around it. Timestamp matching retained as fallback for the 4 real fills that lack the field.

### 0.5 Reference tables — ✅ **SEEDED (MES + ES)**

Contract specs are in **Appendix D**, covering MES today and ES for later. Two caveats carried there: MES option tick values are scaled from the ES figures rather than read off a CME page, and ES option exercise style rests on CME's statement that Micro E-mini procedures mirror their E-mini counterparts. Both are flagged for confirmation before they matter.

### 0.6 Token expiry — ✅ **DONE: 1 year maximum**

Select 1 year. The §3.8 scheduler design stands unchanged.

**But an annual expiry is a once-a-year silent-death risk**, and generating a new token invalidates the current one, so rotation is a deliberate breaking act rather than something that can be automated. Requirements:

- Store `token_issued_at` and `token_expires_at` in config.
- Dashboard warning at **T-30 days**, escalating to a persistent banner at **T-7**.
- On rotation: update config first, then run a manual sync to confirm before trusting the schedule again.
- Error `1012` (token expired) must never be retried and must raise the same alert class as a credential failure (§3.8).

Put the expiry date somewhere a human sees monthly. A token that quietly dies in month eleven produces exactly the stale-but-plausible dashboard §3.8 exists to prevent.

### Build order

Phases in §15 are ordered deliberately: **the reconciliation check (§3.4) must pass before any chart is built.** A dashboard over unverified data is worse than no dashboard, because it looks authoritative. If §0.1 and the Phase 1 reconciliation both come out clean, the data foundation is sound and everything after it is ordinary application work.

---

## 1. Purpose and Scope

### 1.1 Goal

Replicate the *performance-tracking* subset of TradesViz for personal use: import IBKR TradeLog files, reconstruct round-trip trades from raw executions, compute a comprehensive statistics catalog, and present it through an interactive dashboard. Add manual cash-transaction entry so account-level return (not just trade P&L) can be measured.

### 1.2 In scope

| Area | Included |
|---|---|
| Import | **Automated daily sync via IBKR Flex Web Service** (primary, §3.5–3.10); `.tlg` / Flex XML file upload as fallback and backfill; parse, dedupe, idempotent re-import |
| Trade construction | Execution → position → round-trip trade; FIFO lot matching; expirations/assignments |
| Spread grouping | Multi-leg option/FOP strategies treated as a single analytical unit |
| Statistics | ~120 single-number metrics across P&L, risk ratios, streaks, volume, duration, commissions |
| Charts | P&L / win-rate / expectancy vs. ~12 grouping dimensions; distributions; drawdown; equity curve; calendar heatmap |
| Cash tracking | Deposits, withdrawals, starting balance; account value; TWR; XIRR; CAGR |
| Benchmark | Cash-flow-matched SPY buy-and-hold shadow portfolio; alpha, beta, correlation, tracking error (§7.5) |
| Tables | Trades grid, executions grid, symbol-grouped, day-grouped; column show/hide, sort, filter, export |
| Expiry watch | Forward-looking panel of open options by expiry, with resulting-position projection (§9.5) |
| Journaling | Tags, tag groups, per-trade and per-day notes |
| Filtering | Global date range + filter set applied to every chart, stat, and table |

### 1.3 Out of scope (explicit non-goals)

- **AI/LLM features** — no Q&A, no AI coach, no AI-generated widgets, no natural-language querying.
- Trade simulators, backtesters, market replay.
- Signals, alerts, or recommendations of any kind. The Expiry Watch panel (§9.5) is the only forward-looking view and reports mechanical consequences only — no probabilities, no suggested actions.
- Options flow, SEC 13F, fundamentals, seasonality, screeners.
- Live order/position streaming via TWS API or Client Portal Gateway. Statement-level sync only (Flex Web Service), which is sufficient for performance analysis and vastly simpler to operate.
- Registration as an IBKR-listed third-party vendor (see §3.5).
- Multi-user, auth, billing, sharing. Single user, local/LAN deployment.
- Multiple accounts (§14.5.1). Schema carries `account_id` so this is a later UI change, not a migration.
- **Tax reporting of any kind** — no wash sales, no tax-lot elections, no ST/LT split (§14.5.3). FIFO matching serves performance attribution only. State this in the UI footer so it is never mistaken for a tax record.
- Forex, crypto, bonds, CFDs.

### 1.4 Deferred (Phase 4+, requires a market data feed)

These TradesViz features are genuinely useful but **cannot be built from the `.tlg` file alone** — they need historical intraday bars:

- MFE / MAE (maximum favorable/adverse excursion)
- Best-exit P&L, exit efficiency, EOD exit analysis, multi-timeframe exit analysis
- Running/continuous P&L during a trade, positive/negative P&L time
- Unrealized P&L on open positions (needs at minimum an EOD close price)
- Technical-indicator correlation charts (P&L vs. ATR/RSI/ADX at entry)
- Option Greeks analysis (needs IV surface at execution time)

Design the schema with nullable columns for these now so they can be backfilled later without migration pain. See §12.

---

## 2. Source Data: IBKR TradeLog (`.tlg`) Format

### 2.1 Overall structure

Pipe-delimited, section-oriented plain text. Bare uppercase lines are section headers; data lines begin with a record-type tag. Blank lines separate sections. File terminates with `EOF`.

```
ACCOUNT_INFORMATION
ACT_INF|<account_id>|<name>|<account_type>|<address>

STOCK_TRANSACTIONS
STK_TRD|...

OPTION_TRANSACTIONS
OPT_TRD|...

FUTURE_TRANSACTIONS
FUT_TRD|...

FUTURE_OPTION_TRANSACTIONS
FOP_TRD|...

STOCK_POSITIONS
STK_LOT|...

OPTION_POSITIONS
OPT_LOT|...

EOF
```

Sections are **omitted entirely** when they contain no rows. The parser must not assume any section is present. The provided sample contains no `FUTURE_TRANSACTIONS` section because no outright futures were traded in the window — handle `FUT_TRD` on the same code path as `STK_TRD`.

> **Privacy note:** the `ACT_INF` record contains the account holder's legal name and mailing address. Parse the account ID for scoping; **do not persist name or address** to the database or logs. Store a user-supplied account nickname instead.

### 2.2 Transaction records — field layout

`STK_TRD`, `OPT_TRD`, `FUT_TRD`, and `FOP_TRD` share an identical 16-field layout (verified against the sample file):

| # | Field | Type | Notes |
|---|---|---|---|
| 0 | `record_type` | enum | `STK_TRD` / `OPT_TRD` / `FUT_TRD` / `FOP_TRD` |
| 1 | `order_id` | string | **This is IBKR's *order* ID, not an execution ID** — verified: `.tlg` `5358356122` equals Flex `ibOrderID`, while Flex's own `tradeID` for the same fill is `9781749803`. Treat as an opaque string. See §3.2 for why this matters for dedupe. |
| 2 | `symbol` | string | Raw instrument symbol. See §2.4 for per-class parsing. |
| 3 | `description` | string | Human-readable. For options, the reliably parseable form: `ACME 11JUL31 187.05 P` |
| 4 | `exchange` | string | Venue. `--` for non-market events (expiration, assignment). |
| 5 | `action` | enum | `BUYTOOPEN`, `SELLTOOPEN`, `BUYTOCLOSE`, `SELLTOCLOSE` |
| 6 | `open_close_code` | string | `O`, `C`, or `C;Ep` — semicolon-delimited flag list. See §2.3. |
| 7 | `trade_date` | `YYYYMMDD` | |
| 8 | `trade_time` | `HH:MM:SS` | Broker-local (US/Eastern for these venues). Store as `America/New_York`. |
| 9 | `currency` | ISO 4217 | `USD` throughout the sample |
| 10 | `quantity` | signed decimal | **Negative = sell.** Shares for stock; contracts for options/futures. |
| 11 | `multiplier` | decimal | `1` stock, `100` equity option, `5` MES futures option (i.e. $/point) |
| 12 | `price` | decimal | Per-unit execution price |
| 13 | `proceeds` | signed decimal | **`= quantity × multiplier × price`.** Verified exact on all 78 rows of the sample. Note the sign: this is *signed cost*, so a **sell produces a negative** value and a buy a positive one — the opposite of IB's Flex "Proceeds" field. Cash flow is the negation. |
| 14 | `commission` | signed decimal | **Negative = charged to you.** Zero on expiration rows. |
| 15 | `fx_rate_to_base` | decimal | `1.00` for USD-base USD-denominated |

**Critical convention to encode once, centrally:**

```
cash_flow      = -proceeds + commission          # commission already negative
gross_realized = -Σ proceeds   (over a closed round trip)
net_realized   = gross_realized + Σ commission
```

Sanity check on the sample file: `Σ(-proceeds) = 1,549.56`, `Σ commission = -21.62`. Note this figure is **not** the period's realized P&L — it includes cash flows from positions opened before the file window and positions still open at the end. See §2.5.

### 2.3 The `open_close_code` flag list

Field 6 is semicolon-delimited. Observed values in the sample: `O` (38 rows), `C` (30), `C;Ep` (10).

| Flag | Meaning | Handling |
|---|---|---|
| `O` | Opens or increases a position | |
| `C` | Closes or reduces a position | |
| `Ep` | Expired | Price and commission are `0`; exchange is `--`. Close at zero. |
| `A` | Assigned | Generates an offsetting stock `STK_TRD`. Link the two. |
| `Ex` | Exercised | Same as above. |
| `P` | Partial execution | Informational |
| `L` | Long-term (tax lot age) | Ignore for performance purposes |

Parse as a **set of flags**, never as a fixed string. Unknown flags: log a warning, do not fail the import.

### 2.4 Symbol parsing

Three distinct schemes. Normalize all of them into an `instruments` row (§4.2).

**Stock / ETF** — field 2 is the plain ticker (`BIDM`, `BCGX`). Nothing to parse.

**Equity option** — field 2 is OCC 21-character format: 6-char root (space-padded) + `YYMMDD` + `C|P` + 8-digit strike with 3 implied decimals.

```
"ACME  310711P00187050"
 ^root ^exp   ^ ^strike/1000
root   = "ACME",  expiry = 2031-07-11,  right = PUT,  strike = 187.050
```
Cross-check against field 3 (`ACME 11JUL31 187.05 P`) and log a discrepancy rather than silently trusting one.

**Futures option** — field 2 is IB's local symbol (`EX1Q6 P7120`, `X3DN6 P7410`), which encodes an internal weekly-series code and is **not** reliably parseable. Field 3 is regular (`MES 08AUG31 3061.6 P`) and is the `.tlg` fallback.

> **When syncing via Flex, do not parse these at all.** The `SecurityInfo` section supplies every field directly — verified for all 11 MES instruments: `symbol=EX1Q1 P3061.6`, `underlyingSymbol=MESU1`, `underlyingConid=700793356`, `multiplier=5`, `strike=7120`, `expiry=20310808`, `putCall=P`. Build the `instruments` table from `SecurityInfo` keyed on `conid`, and keep description-parsing only as the `.tlg`-only fallback path.
>
> Note `underlyingSymbol` is **`MESU1`** — the specific September 2031 futures contract, not the generic `MES` root. That is correct and useful: assignment (§5.4) delivers *that* contract, and grouping by underlying should not merge positions across different underlying futures months. Store both `underlying_symbol` (`MESU1`) and a derived `underlying_root` (`MES`).

**Outright futures** (not in sample) — expect field 3 of the form `MES SEP26` or similar. Parse root + contract month; fall back to storing the raw string with `parse_status = 'unparsed'` and surface it in an import-warnings panel rather than dropping the row.

### 2.5 Position records (`STK_LOT`, `OPT_LOT`)

End-of-period snapshot, 12 fields:

| # | Field | Notes |
|---|---|---|
| 0 | `record_type` | `STK_LOT` / `OPT_LOT` |
| 1 | `account_id` | |
| 2 | `symbol` | Same encoding as transactions |
| 3 | `description` | |
| 4 | `currency` | |
| 5 | `open_date` | Empty in sample |
| 6 | `open_time` | `00:00:00` in sample |
| 7 | `quantity` | Signed position size |
| 8 | `multiplier` | |
| 9 | `cost_basis_price` | Per-unit, **commission-inclusive** |
| 10 | `cost_basis_money` | `= quantity × multiplier × cost_basis_price` |
| 11 | `fx_rate_to_base` | |

Use these for **import reconciliation** (§3.4). Worked example from the sample: BIDM shows 20 shares bought at 124.924675 with $0.43 commission, then 8 shares sold. `STK_LOT` reports cost basis 124.94617629/share on 12 shares = 1,499.35. That equals `12 × (124.924675 + 0.4300258/20)` — IB allocates the *opening* commission across the opening lot and does not net closing commissions into the remaining basis. Match this convention.

### 2.6 Known gaps in the format

The `.tlg` export does **not** contain:

| Missing from `.tlg` | Resolution |
|---|---|
| Cash transactions (deposits, withdrawals, interest, fees) | **Available in Flex** (`CashTransactions` section) → automatic, §3.9. Manual entry retained as a fallback, §7 |
| Dividends | **Available in Flex** (`CashTransactions`, type `Dividends`) |
| Corporate actions (splits, mergers, symbol changes) | **Available in Flex** (`CorporateActions` section) |
| Order-level grouping (which legs were one combo order) | **Likely available in Flex** via `ibOrderID` — verify, §3.9. Falls back to timestamp inference, §6 |
| Realized P&L per closed lot (IB's own calculation) | **Available in Flex** at `levelOfDetail=CLOSED_LOT` → use to validate trade construction, §3.4 |
| Market prices of any kind | Not available from either. Needs an external feed, §12. (Flex does give a `closePrice` per open position — enough for basic unrealized P&L) |
| Futures tick size (only the dollar multiplier is given) | Static instrument reference table, §4.2 |

Also note the file covers a **window** (`20310602`–`20310808` in the filename). Positions opened before the window appear only as orphan closes — the sample has a `SELLTOCLOSE` of 100 BCGX with no matching open. §5.5 covers handling.

---

## 3. Data Acquisition and Import Pipeline

### 3.1 Flow

```
[fetch via Flex API | file upload] → checksum → parse → validate → stage → normalize instruments
       → upsert executions (idempotent) → rebuild trades → recompute metrics
       → reconcile vs. LOT records → import report
```

### 3.2 Requirements

- **Idempotent.** Re-importing the same file, or an overlapping window, must not duplicate. Overlapping date ranges across files are expected and normal.

  **Dedupe keys differ by source, and mixing them will double-count.** The `.tlg` exposes only the order ID; Flex exposes `transactionID`, `tradeID`, `ibExecID`, and `ibOrderID`. Store all identifiers you receive, and dedupe on a canonical key resolved in this order:

  1. `transactionID` (Flex) — unique per execution, verified 78/78 distinct
  2. `ibExecID` (Flex)
  3. `ibOrderID` — the only key a `.tlg` row carries

  A `.tlg` row and a Flex row for the same fill match **only** via `ibOrderID`. In the validation set every order had exactly one execution (78 distinct `ibOrderID` across 78 executions), so this is currently 1:1 — but a **partially filled order breaks that assumption**, producing multiple Flex executions under one `ibOrderID` and one aggregated `.tlg` row. Treat `ibOrderID` as non-unique by design: on collision, prefer the Flex rows and mark the `.tlg` row superseded rather than inserting both.
- **Atomic.** Wrap in a transaction; roll back the whole import on a fatal parse error. Non-fatal issues (unknown flag, unparseable futures symbol) are warnings that still import the row.
- **Auditable.** Persist every raw line with its source file ID and line number. Never mutate raw rows — all derived data is recomputed from them. This is what makes the trade-construction algorithm safe to change later.
- **Store the file.** Keep the original upload on disk keyed by SHA-256, so a full rebuild is always possible.

### 3.3 Import report (shown after every upload)

- Rows parsed per section; rows inserted vs. skipped as duplicates
- Date range covered; new trades opened, closed, and modified
- Warnings: unparsed symbols, unknown flags, orphan closes
- Reconciliation result (§3.4)

### 3.4 Reconciliation

After rebuilding, compare computed open positions against the `STK_LOT` / `OPT_LOT` snapshot:

| Check | Tolerance | On failure |
|---|---|---|
| Position quantity per instrument | exact | **Error** — flag prominently; almost always a missing prior-window file |
| Cost basis per instrument | ±$0.01 | Warning |
| Instruments in LOT but not in computed positions | — | Error, prompt for earlier file |

This check is the single most valuable correctness feature in the app. It catches missing history before it silently corrupts every downstream statistic.

**Additional check when syncing via Flex:** request `levelOfDetail=CLOSED_LOT` alongside `EXECUTION`. IB then returns its own FIFO lot matching with `fifoPnlRealized` per lot. Assert that your computed per-trade realized P&L matches IB's to ±$0.01. This is a far stronger correctness guarantee than the position snapshot alone — it validates the entire matching algorithm in §5.2 against the broker's own books, and it is free.

---

### 3.5 Automated Sync: Which IBKR Mechanism

IBKR offers three distinct paths to get statement data out automatically. They are easy to confuse because all three live under "Third-Party Reports" in the portal navigation.

| | **Flex Web Service** | **Reporting Integration feed** | **Listed third-party vendor** |
|---|---|---|---|
| Direction | You pull over HTTPS | IBKR pushes to FTP/sFTP (or web service) | Vendor pulls with their own credentials |
| Setup | Self-service, ~5 min | Email request + config negotiation with IBKR | Compliance review + legal agreement |
| Lead time | Immediate | Days to weeks | ~2–3 months |
| Infrastructure | An HTTP client | sFTP endpoint or FTP poller + PGP decryption | None (it's not your app) |
| Credentials | Token (expiring) + Query ID | RSA key-based auth + PGP key pair | OAuth 1.0a consumer |
| Data richness | High | **Highest** — see §3.11 | N/A |
| Eligibility | Any account | Ask — see §3.11 caveat | Commercial vendors only |
| **Verdict** | **Build on this** | **Request in parallel; adopt if granted** | Ruled out |

**The vendor path is out.** The Third-Party Services checkbox list (TradesViz, TraderSync, Sharesight, Plaid…) authorizes *those companies'* pre-provisioned credentials. Getting your app onto that list means IBKR third-party onboarding: enhanced due diligence, a three-tier approval process estimated at 3–6 weeks for compliance, plus a further 3–5 weeks for the Web API agreement, OAuth 1.0a consumer registration, public keys, and a callback URL. That is a commercial-vendor process, not a personal-project one.

**Flex Web Service is the right foundation.** The portal page points at it: for a proprietary program requiring a set format, use Activity Flex Queries. Note also that the TurboTax integration uses a Token and Query ID — the identical credential pair. Same plumbing, self-directed. Details in §3.6–3.10.

**The Reporting Integration feed is a real upgrade if you can get it**, and requesting it costs one email. §3.11 covers what it adds and what it costs.

**Consequence for the spec either way:** file upload stops being the primary path and becomes the fallback. Everything downstream of the parser is unchanged — which is exactly why §4.1's parser registry keyed on `source_format` matters. A third ingestion adapter is a drop-in.

### 3.6 One-Time Setup (manual, documented in the app's README)

1. Client Portal → **Performance & Reports → Flex Queries**.
2. Create an **Activity Flex Query** (not a Trade Confirmation Flex) with the sections in §3.9. Save the **Query ID**.
   - Set **Format** to **XML**. This is a separate setting from the period, it defaults to text, and nothing in the request says which one will come back — a CSV-configured query answers `SendRequest` and `GetStatement` with two cheerful 200s and a body this app cannot read. Verified the hard way on the first live sync; the client now detects the CSV signature and names this setting, but the setting is still the fix.
3. **Performance & Reports → Flex Queries → Flex Web Service Configuration.** Tick Flex Web Service Status, Save.
4. Generate a token. Two settings matter:
   - "Should Expire After" — the token is valid for 6 hours by default. Pick the **longest available option**; a 6-hour token makes unattended sync impossible.
   - "Valid For IP Address" — restricting the token to one address. Set this if the app runs on a host with a static IP. It converts a leaked token from a full statement-history disclosure into a much smaller problem.
5. Generating a new token invalidates the current one — so rotation is a deliberate, breaking act. The app must handle a suddenly-invalid token gracefully rather than silently stopping (§3.8).

Store Query ID and token in app config. The app never automates step 3–5; there is no API to mint tokens.

### 3.7 Protocol

Two-step, asynchronous: request generation, then poll for the result.

**Step 1 — SendRequest**

```
GET https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest
    ?t={token}&q={queryId}&v=3
```

Optional overrides, both capped at 365 days: `&fd=yyyymmdd&td=yyyymmdd` for an explicit range, or `&p=N` for a trailing period.

Programmatic access requires a `User-Agent` header set to the technology and version in use (e.g. `Python/3.12`). Requests without it are rejected. This trips up nearly every first implementation — `requests` sets its own default, so set it explicitly.

Success returns `<Status>Success</Status>`, a `<ReferenceCode>`, and a `<url>`. **Use the returned `<url>` rather than hardcoding the GetStatement endpoint** — IBKR has moved these hosts before (the older `gdcdyn`/`Universal/servlet` forms still appear in circulating documentation and sample code).

**Step 2 — GetStatement**

```
GET {url_from_step_1}?t={token}&q={referenceCode}&v=3
```

Returns the Flex XML on success, or a `<FlexStatementResponse>` with `<Status>Fail</Status>` and an error code.

**Always specify `v=3`.** Omitting the version silently falls back to Version 2, which has a different response shape.

### 3.8 Sync Scheduler

**Cadence.** Once daily, plus an on-demand "Sync now" button. Schedule for ~05:00 ET: IB's overnight settlement and P&L processing must complete before the statement is final, and the error codes below exist precisely because it often isn't ready earlier. Skip weekends and market holidays, but run Monday over the full weekend range so Sunday-evening futures sessions are captured.

**Window.** Request a trailing 30 days on every run, not "since last sync." Trades can be amended, and same-day busts or corrections will otherwise never be picked up. Idempotent upsert on `broker_trade_id` (§3.2) makes the overlap free. For initial backfill, chunk into ≤365-day windows and walk backwards.

**Error handling.** Classify by code and never blindly retry everything:

| Codes | Class | Action |
|---|---|---|
| 1001, 1004, 1005, 1006, 1007, 1008, 1009, 1019, 1021 | Transient — statement/P&L data not ready or server busy | Exponential backoff, jitter, up to ~6 attempts over ~30 min, then retry on the next scheduled run |
| 1018 — limited to one request per second and ten per minute per token | Rate limit | Hard-throttle to well under that; back off and resume |
| 1012 (token expired), 1015 (token invalid), 1013 (IP restriction), 1011 (service account inactive) | Credential — needs human action | **Do not retry.** Raise a visible, persistent alert in the UI and via whatever notification channel you use |
| 1014 (query invalid), 1010 (legacy Flex no longer supported), 1016 (account invalid) | Configuration | Do not retry; alert |
| 1003 (statement not available), 1017 (reference code invalid) | Request-specific | Log, restart from SendRequest once, then give up for this run |

**The failure mode that matters** is a silently dead sync: the token expires, nightly runs fail for three weeks, and the dashboard keeps showing stale numbers that look plausible. Guard against it with a **staleness banner** — persistent and unmissable whenever the last successful sync is older than two business days — plus a `flex_sync_runs` table recording every attempt (started_at, status, error_code, rows_ingested, window requested). Show last-successful-sync time in the UI header at all times.

**Poll etiquette.** Between SendRequest and GetStatement, wait ~3 seconds before the first poll, then back off. Generation typically takes a few seconds; hammering it just triggers 1018.

### 3.9 Flex Query Configuration

Create one Activity Flex Query in **XML** format with these sections. Field names below are from the Flex schema; **verify them against your first generated XML** rather than trusting this list, as IBKR revises the schema periodically.

| Section | Level of detail | Purpose |
|---|---|---|
| **Trades** | `EXECUTION` | Primary execution feed — replaces `STK_TRD`/`OPT_TRD`/`FUT_TRD`/`FOP_TRD` |
| **Trades** | `CLOSED_LOT` | IB's own FIFO lot matching + `fifoPnlRealized` → reconciliation (§3.4), with the assignment carve-out in §5.4 |
| **Options, Exercises, Assignments and Expirations** | — | Exercise, assignment and expiration activity for stocks, options, futures, futures options and structured products, plus cash settlement for index options; grouped by Assignments, Exercises and Expirations. **Authoritative source for §5.4** |
| **Transfers (ACATS, Internal)** | — | Position transfers in and out → §5.5. Without this, every transferred position looks like an orphan close |
| **Cash Transactions** | — | Deposits, withdrawals, dividends, interest, fees — removes the manual-entry requirement in §7 |
| **Open Positions** | `LOT` | Replaces `STK_LOT`/`OPT_LOT`; includes `closePrice`/`markPrice` → daily unrealized P&L with no external feed |
| **Financial Instrument Information** | — | Contract reference: multiplier, expiry, strike, right, underlying, `conid` → **eliminates §2.4 symbol parsing**, including the unparseable futures-option local symbols |
| **Change in NAV** + **Net Asset Value (NAV) Summary in Base** | — | Account value decomposition and period-over-period NAV → validates §7.2–7.3 |
| **Realized and Unrealized Performance Summary in Base** | — | IBKR's own realized/unrealized split per instrument → second independent check on §8.1 |
| **Unbundled Commission Details** | — | **Verified present.** Per-execution fee itemization: broker execution/clearing, third-party, FINRA TAF, SEC Section 31, other → resolves §14.5.4 in v1 |
| **Corporate Actions** | — | Splits, symbol changes → feeds §11.3 |
| **Statement of Funds** | — | Complete cash ledger; ties account value out exactly |
| **Account Information** | — | Account ID and base currency only |

Set **Date Format `yyyyMMdd`**, **Time Format `HHmmss`**, **Date/Time Separator: semicolon or space**, and enable **"Include Header/Trailer Records"**.

**Period — and the date override may simply not be available.** The advice that stood here previously, "choose Period: Last N Calendar Days so the `p`/`fd`/`td` overrides behave predictably", was wrong. A query whose stored period is a relative one answers **every** `fd`/`td` request with `1003`, *Statement is not available*, at SendRequest, before any statement exists.

Verified against query 1569573, stored as Last 90 Calendar Days: it generates correctly in Client Portal, succeeds over the web service with no override, and fails `1003` on a 30-day `fd`/`td` sitting well inside its own 90-day period.

**The obvious remedy is not available, and the reason is worth writing down**, because Client Portal shows Custom Date Range in a place where it does nothing for us. There are two Period dropdowns and they are not the same control:

| Where | Offers Custom Date Range? | Governs the web service? |
|---|---|---|
| Edit query → **Delivery Configuration** ("Applicable for Email, FTP and Flex Web Service") | **No** — relative periods only: Last Business Day/Week/Month/Quarter, Last 30/365/N Calendar Days, Month/Quarter/Year to Date | **Yes** |
| The ad-hoc **Run** dialog | Yes, as the first entry | No — that run happens in the browser |

So the API reads the one list that cannot express a custom range. Seeing Custom Date Range while running the query by hand is not evidence the override will work, and is a genuinely easy thing to be misled by.

**Consequences.** Two things wanted the override:

- The trailing 30-day sync window (§3.8). This degrades: when SendRequest answers `1003` to an overridden request, the sync asks again with no date parameters and takes the query's own period, recording the window the statement actually covers rather than the one it asked for. Set the query's period to at least 30 days — 90 is comfortable — since the fallback can only take what the query offers.
- The single-day request behind "fetch from IBKR" for the epoch NAV (§0.3), which needs `fd=td=<epoch date>` to read `ChangeInNAV.startingValue` for one specific day. **This has no fallback** — a relative period cannot name an arbitrary past day — so on such a query the NAV has to be typed, and §0.3's warning about hand-typed NAVs applies with full force.

The failure is nastier than its text suggests: `1003` is also what a genuinely unavailable statement returns, and nothing in the message mentions the query. One request each way separates them, which is what `python -m app.flex.diagnose` (§3.8) exists to do.

**Fields worth requesting explicitly on Trades** (superset of what `.tlg` gives):

`tradeID`, `ibExecID`, `ibOrderID`, `orderReference`, `transactionID`, `accountId`, `assetCategory`, `symbol`, `underlyingSymbol`, `description`, `conid`, `securityID`, `multiplier`, `strike`, `expiry`, `putCall`, `tradeDate`, `dateTime`, `orderTime`, `settleDateTarget`, `transactionType`, `exchange`, `quantity`, `tradePrice`, `tradeMoney`, `proceeds`, `ibCommission`, `ibCommissionCurrency`, `taxes`, `netCash`, `closePrice`, `openCloseIndicator`, `notes`, `cost`, `fifoPnlRealized`, `mtmPnl`, `origTradeID`, `orderType`, `isAPIOrder`, `levelOfDetail`, `changeInPrice`, `changeInQuantity`

Three of these are worth calling out:

- **`ibOrderID`** — legs of a combo order should share it. If that holds in your data, spread grouping (§6) becomes *deterministic* rather than a timestamp heuristic, which is a meaningful accuracy win given that verticals are essentially all you trade. **Validate this against a known multi-leg fill before relying on it**, and keep the timestamp heuristic as a fallback for legs entered separately.
- **`notes`** — the Flex equivalent of the `.tlg` `open_close_code` flag list (`Ep`, `A`, `Ex`, `P`, `C`, `O`). Same parsing rules as §2.3.
- **`conid`** — IBKR's stable contract identifier. Use it as the natural key for `instruments` (§4.2) instead of symbol strings. It survives symbol changes and eliminates the futures-option local-symbol parsing problem in §2.4 entirely.

**Sign conventions — verified exactly, 0 violations across 78 executions:**

```
Flex tradeMoney  ==  .tlg proceeds  ==  quantity x multiplier x price
Flex proceeds    == -tradeMoney                    # cash-flow sign: sells positive
Flex netCash     ==  proceeds + ibCommission       # commission already negative
```

So the two formats' fields named `proceeds` are **negatives of each other**. Map `.tlg proceeds -> tradeMoney` at the parser boundary and derive everything downstream from the canonical form. **Do not share arithmetic between the two parsers**, and unit-test each against the same known trade (test 26).

Worked example, BCGX sell of 100 @ 25.5721: `tradeMoney = -2557.21`, `proceeds = 2557.21`, `ibCommission = -0.491192526`, `netCash = 2556.718807474`.

### 3.10 Credential Security

The token is a bearer credential granting read access to your full statement history — positions, P&L, account value, and, via Account Information, personally identifying data.

- Never in source control, never in a container image, never in a URL you paste anywhere. Note that the token travels as a **query parameter**, so it will appear in any proxy or webserver access log in the path — suppress request-path logging on this route.
- Store encrypted at rest: OS keyring, `sops`/`age`, or a Docker secret. Environment variables are acceptable for a single-host personal deployment; a plaintext file in the repo directory is not.
- Set the IP restriction if the host has a static address.
- Log the *fact* of a request, never the URL.
- Redact the token from any error message before it reaches the UI or a log file — failure responses are the most common accidental-disclosure path.

### 3.11 Reporting Integration Feed — Evaluated, Not Adopted

**Decision: not pursued.** Recorded here so the reasoning isn't relitigated later.

A separate IBKR facility: a daily reporting data feed delivered as flat files. Requested by emailing `reportingintegration@interactivebrokers.com`; IBKR prepares the files and transmits them via FTP. It provides seven start-of-day file types — Account, Activity, Cash Report, NAV, P&L, Positions, Securities — plus an end-of-day Trade Confirms file.

**Why declining costs little.** Flex covers nearly all of it (§3.9):

| Reporting feed file | Flex equivalent | Gap |
|---|---|---|
| Positions (`MarketPrice`, `UnrealizedPL`) | Open Positions with `markPrice`/`closePrice` | None material |
| NAV (incl. IBKR-computed `TWR`) | Change in NAV + NAV Summary in Base | IBKR's own TWR figure isn't handed to you; §7.3 computes it. A cross-check you forgo, not a capability |
| P&L (MTM, realized/unrealized ST/LT) | Realized and Unrealized Performance Summary in Base | ST/LT split is tax-oriented and out of scope anyway (§14.5.3) |
| Securities (contract reference) | Financial Instrument Information | None |
| Trade Confirms (itemized commissions) | Commission Details / Transaction Fees | None material |

**What is actually forgone:** push delivery (no scheduler or token rotation to operate), and same-day EOD trade confirms rather than next-morning statements. Neither justifies standing up an sFTP endpoint with PGP key management for a single-user app — and PGP encryption is mandatory for FTP/sFTP delivery, with an RSA key and whitelisted IPs for IB-hosted sFTP.

**Revisit only if** Flex token rotation becomes a recurring operational annoyance, or if the account structure changes such that push delivery is genuinely simpler. The parser-registry design in §4.1 keeps the door open at low cost.

---

## 4. Data Model

Twelve core tables. SQLite is sufficient; Postgres if you want window functions for the analytics queries (recommended — see §13).

### 4.1 `import_files`
`id, source (FLEX_SYNC|FILE_UPLOAD), source_format (TLG|FLEX_XML), layout_version, filename, sha256, retrieved_at, account_id, period_start, period_end, row_count, status, warnings_json, flex_sync_run_id (nullable)`

`layout_version` records the source's declared schema version so a silent format change is caught at parse time rather than surfacing as mysterious sign errors three weeks later. The enums are deliberately open — §3.11's feed was declined, but the parser registry means adding a format is a new value plus a new adapter.

Every Flex payload is written to disk and registered here exactly as an uploaded file would be. The sync path and the upload path converge immediately after retrieval — one parser registry keyed on `source_format`, one pipeline thereafter.

### 4.1b `flex_sync_runs`
`id, started_at, finished_at, trigger (SCHEDULED|MANUAL|BACKFILL), window_from, window_to, status (SUCCESS|TRANSIENT_FAIL|CREDENTIAL_FAIL|CONFIG_FAIL), attempt_count, error_code, error_message_redacted, reference_code, rows_ingested, rows_new`

Backs the staleness banner and the sync-history panel (§3.8).

### 4.2 `instruments`
`id, asset_class (STK|OPT|FUT|FOP), root_symbol, broker_symbol, broker_local_symbol, description, currency, multiplier, tick_size (nullable, from reference table), expiry (nullable), strike (nullable), right (C|P|null), exercise_style (AMERICAN|EUROPEAN|UNKNOWN), settlement (PHYSICAL|CASH|INTO_FUTURE|null), underlying_root, underlying_conid, parse_status`

Unique on `(broker_symbol, asset_class)`.

Seed a static reference for futures tick sizes: MES = 0.25 pt = $1.25, ES = 0.25 = $12.50, MNQ = 0.25 = $0.50, NQ = 0.25 = $5.00, etc. Needed for points/ticks statistics.

### 4.3 `executions`
`id, import_file_id, source_line, account_id, broker_trade_id (unique per account), instrument_id, action, flags_json, executed_at (tz-aware), trading_day (derived, §14.1), quantity, multiplier, price, proceeds, commission, fx_rate, cash_flow (computed), exchange, related_execution_id (nullable, §5.4)`

Every `GROUP BY` over days uses `trading_day`, never `date(executed_at)` — deriving it twice is how the two halves of a dashboard end up disagreeing.

### 4.4 `trades` (round trips — derived, rebuildable)
```
id, account_id, instrument_id, side (LONG|SHORT), status (OPEN|CLOSED|PARTIAL|ORPHAN_CLOSE),
origin (TRADED|TRANSFERRED|PRE_EPOCH|MANUAL), straddles_epoch (bool), book (default from §6.5 rules), basis_policy_applied (nullable),
opened_at, closed_at, duration_seconds,
open_qty, total_buy_qty, total_sell_qty, remaining_qty,
avg_open_price, avg_close_price,
gross_pnl, commissions, fees, net_pnl,
cost_basis, return_pct, pnl_per_qty, pnl_points, pnl_ticks,
stop_loss (nullable, manual), profit_target (nullable, manual), r_value (nullable),
execution_count, trade_group_id (nullable),
-- Phase 4 placeholders, all nullable:
mfe_price, mae_price, mfe_pct, mae_pct, best_exit_pnl, exit_efficiency,
eod_exit_pnl, max_running_pnl, min_running_pnl, unrealized_pnl, last_eod_price
```

### 4.5 `trade_executions`
Join table: `trade_id, execution_id (nullable), matched_qty, leg_role (OPEN|CLOSE), synthetic (bool), synthetic_source (TRANSFER|MANUAL_BASIS|null)`. A single execution can be split across multiple trades under FIFO — carry the matched quantity here, not on the execution. Synthetic rows have no `execution_id` and must be visually distinguished in the executions grid so a reconstructed opening leg is never mistaken for a real fill.

### 4.6 `trade_groups` (spreads / strategies)
`id, account_id, strategy_type, book, underlying_root, opened_at, closed_at, net_debit_credit, max_risk (nullable — see §6.3), margin_requirement (nullable, manual), post_assignment_capital (nullable), gross_pnl, net_pnl, return_on_risk (nullable), return_on_margin (nullable), return_on_post_assignment_capital (nullable), leg_count, assigned (bool), parent_group_id (nullable), auto_detected (bool), user_confirmed (bool)`

`strategy_type` includes `ASSIGNMENT_CHAIN` (§5.4). `parent_group_id` links a chain back to the spread that produced it, so the original position and its post-assignment life are both individually and jointly analyzable.

### 4.6b `settings` (single row)
`tracking_start_date, tracking_start_value, tracking_start_value_source (FETCHED|MANUAL), tracking_start_value_ibkr (nullable, for cross-check), transfer_basis_policy, session_start_hour, default_book, benchmark_symbol, gross_or_net, token_issued_at, token_expires_at`

`tracking_start_value_ibkr` is refreshed on each sync from a date-overridden Flex request so divergence from the stored value is always visible (§0.3). Editing `tracking_start_date` triggers a full rebuild.

### 4.7 `cash_transactions`
`id, account_id, occurred_on, type (DEPOSIT|WITHDRAWAL|TRANSFER_IN|TRANSFER_OUT|DIVIDEND|INTEREST|FEE|ADJUSTMENT), amount (signed), currency, source (FLEX|MANUAL), related_instrument_id (nullable), broker_transaction_id (nullable, unique), note`

`TRANSFER_IN`/`TRANSFER_OUT` carry the position's transfer-date market value and are external flows for TWR purposes (§5.5, §7.3) — never trading P&L. `source` prevents a resync from overwriting manually-entered corrections.

### 4.7b `position_transfers`
`id, account_id, transferred_on, direction (IN|OUT), transfer_type (ACATS|ATON|INTERNAL|OTHER), instrument_id, quantity, transfer_price, position_amount, reported_pl, original_cost_basis (nullable), broker_transaction_id, counterparty, basis_verified (bool), note`

Kept separate from `cash_transactions` because the synthetic opening leg in §5.5 needs the per-unit detail, and because `original_cost_basis` is often absent or wrong on arrival and gets corrected by hand later.

### 4.8 `tags` / `trade_tags` / `day_tags`
`tags: id, name, group_name, color`. Join tables for trades and for calendar days.

### 4.9 `notes`
`id, scope (TRADE|DAY|GENERAL), scope_ref, body_md, created_at, updated_at`

### 4.10 `price_bars` (benchmark and future market data, §7.5)
`symbol, date, close, adj_close, source, fetched_at` — primary key `(symbol, date)`.

Populated incrementally by the `PriceSource` adapter. The only table any benchmark or mark-to-market calculation reads from; no code calls a price API directly. `source` records which provider supplied each row so a provider swap is auditable rather than silently mixed.

### 4.11 `daily_snapshots` (materialized, rebuilt on import)
`trading_day, realized_gross, realized_net, commissions, trade_count, win_count, loss_count, volume, cumulative_net_pnl, cash_flow, account_value, drawdown, drawdown_pct, benchmark_shadow_value (nullable)`

Keyed on `trading_day` (the §14.1-derived day), never on calendar date.

This table backs the equity curve, calendar heatmap, drawdown chart, and all daily-frequency ratios (Sharpe, Sortino, Omega). Precompute it; do not derive on every request.

---

## 5. Trade Construction

### 5.1 Definition

A **trade** is a round trip in one instrument: a sequence of executions taking a position from flat, through open, back to flat. Reopening the same instrument after going flat starts a **new** trade. This matches TradesViz's model and is what makes win rate, duration, and expectancy meaningful.

### 5.2 Algorithm

For each `instrument_id`, executions ordered by `executed_at`, then `broker_trade_id`:

```
running_qty = 0
current_trade = null

for exec in executions:
    if running_qty == 0:
        current_trade = new Trade(side = LONG if exec.qty > 0 else SHORT)

    if sign(exec.qty) == sign(running_qty) or running_qty == 0:
        # opening or adding
        push_open_lot(exec.qty, exec.price, exec.commission)
        attach(current_trade, exec, role=OPEN)
    else:
        # closing — FIFO match against open lots
        remaining = abs(exec.qty)
        while remaining > 0 and open_lots:
            matched = min(remaining, open_lots[0].qty)
            realize(matched, open_lots[0].price, exec.price)
            attach(current_trade, exec, role=CLOSE, matched_qty=matched)
            remaining -= matched; consume(open_lots[0], matched)

        if remaining > 0:
            # position flipped through zero
            close_trade(current_trade)
            current_trade = new Trade(side = flipped)
            push_open_lot(remaining * sign(exec.qty), exec.price, 0)

    running_qty += exec.qty
    if running_qty == 0:
        close_trade(current_trade)
```

**Lot matching: FIFO — decided, and fixed.** IBKR's default and what its own reports use, so your numbers reconcile against IB's statements and against `fifoPnlRealized` (§3.4). Ship FIFO only; do not expose LIFO or average-cost as a setting. A matching method that can be changed after trades are stored is a footgun — it silently invalidates every historical statistic, and no user of a single-account personal tool benefits from the option. If it ever becomes necessary, it is a versioned rebuild, not a toggle.

### 5.3 Position flip

A single execution that crosses zero (e.g. long 5, sell 8) closes the first trade at the crossing point and opens a new one with the residual. Split the execution across both trades via `trade_executions.matched_qty`.

### 5.4 Expirations, Assignments, and Exercises

Not present in the sample file, but **in scope**, and the exposure is concentrated in one place: **naked short futures options**. Defined-risk verticals cap the damage of an assignment; a naked short MES put does not. As of the fixture there are five such positions, so this handling must exist before it fires, not be retrofitted during a live assignment.

**What assignment on a naked MES put produces:** a **long** MES futures position at the strike — roughly `7120 × 5 = $35,600` notional per contract, replacing an option that required a fraction of that in margin. The app must show this as a linked chain (below) rather than as an unexplained futures position appearing overnight, and must not report the group's original `max_risk` as though it still applied (§6.3).

#### Exercise style — resolved

Exercise style is a contract specification, not a configuration choice. For the instruments currently traded:

| Instrument | Style | Early assignment possible? |
|---|---|---|
| **MES weekly + EOM options** (all symbols in the fixture: EX1–EX4 Friday weeklies, EXM6/EXN6 end-of-month, X1B/X3D Tue/Thu weeklies) | **European** — CME lists Monday–Friday Weekly and EOM options as European; only Quarterly AM Expiry options are American | **No.** Positions cannot be exercised before expiration, eliminating unexpected early assignments |
| **MES quarterly options** (not currently traded) | American | Yes |
| **US equity options** (ACME, MEMC, VERT, BIDX, SEMX, OMNI) | American | **Yes** |

**Store `exercise_style` on `instruments`** (§4.2), populated from a static rule table keyed on product + series, defaulting to `UNKNOWN` with a warning rather than guessing. Every behaviour below branches on it.

**Consequence, and a correction to an earlier reading of this data:** *early*-assignment exposure sits with the **American-style equity options** — specifically the naked short puts, since a defined-risk vertical caps the outcome. The naked MES positions carry the larger *at-expiry* consequence (a ~$35,600 notional futures position per contract) but on a **known, deterministic date**. Those are different problems needing different handling: the equity side needs surprise detection (the banner below); the futures side needs forward-looking scheduling (§9.5).

#### At-expiry mechanics for European futures options

Exercisable only on expiration day; options in-the-money on the last day of trading are automatically exercised, and exercise results in a position in the underlying futures contract. No abandonment is allowed.

ITM is determined by a 3 p.m. CT price fixing based on the weighted-average traded price of the E-mini futures over the last 30 seconds of trading on expiration day (2:59:30–3:00:00 p.m. CT) — **not the closing price**.

> **Never infer expiry outcome from market data.** A position can appear OTM on a price feed and still exercise on the fixing. The broker's `Ep` / assignment records are the only authority; the app reports what settled, never what it calculates should have settled. This also means §9.5's panel is explicitly a *forecast*, and must be labelled as one.

#### Expiration

`Ep`-flagged rows close at price 0, commission 0. Real closes; include them. The sample has 10 such rows, and 4 of the ACME/MES spreads terminate this way. A spread expiring worthless is a *win* on the short leg and a *loss* on the long leg — only the group view (§6) shows the true result.

#### Detection

Two sources, use both:

1. **Flex `notes` / `.tlg` `open_close_code` flags** — `A` (assignment), `Ex` (exercise), `Ep` (expired). Parse as a flag set (§2.3).
2. **Flex "Options, Exercises, Assignments and Expirations" section** — authoritative. Covers exercise, assignment and expiration activity for stocks, options, futures, futures options and structured products, plus cash settlement for index options, grouped by Assignments, Exercises and Expirations, and displays the underlying for each contract.

Assignment appears in the *next* statement, so the daily sync (§3.8) picks it up a day after the fact. **Surface it at the top of the import report and as a persistent dashboard banner until acknowledged** — an unexpected futures or stock position is exactly the thing you want to be told about rather than discover. This is the single highest-value notification in the app; do not bury it in a warnings list.

#### Linking

An assignment or exercise produces **two** records: the option closing and the resulting underlying position. Match them on:

- Same date
- Option's `underlying_conid` == underlying instrument's `conid`
- `|underlying_qty|` == `|option_qty| × multiplier`
- Direction consistent with the leg:

| Position | Event | Underlying result |
|---|---|---|
| Short put | Assigned | **Long** shares at strike |
| Short call | Assigned | **Short** shares at strike |
| Long put | Exercised | **Short** shares at strike |
| Long call | Exercised | **Long** shares at strike |
| Short/long futures option | Assigned/exercised | Futures position at strike, 1 contract per option |
| Cash-settled index option | Settled | Cash adjustment at intrinsic value; no position |

Record the link on `executions.related_execution_id` in both directions.

#### P&L convention — the decision that matters

Two defensible treatments:

| | **(a) Separate + grouped** *(recommended)* | **(b) Rolled basis** *(IB / tax convention)* |
|---|---|---|
| Option leg | Closes at 0 → realized P&L = full premium | No separate realized P&L |
| Underlying | Opens at strike | Opens at strike ∓ premium |
| Answers "how did my short puts perform?" | Yes, directly | No — premium is buried in stock basis |

**Use (a).** This tool exists to attribute performance to decisions, and convention (b) makes the premium invisible to every option statistic in §8. Store the rolled-basis figure alongside for reference.

> **Reconciliation carve-out.** IBKR uses convention (b). Acceptance test 27 (per-trade P&L matches `fifoPnlRealized` to ±$0.01) **will fail on assignment chains by design**. Reconcile those at the *chain* level instead: the sum of option P&L plus underlying P&L under (a) equals IB's figure under (b). Encode this as an explicit exception with its own test, not as a loosened tolerance — a widened tolerance hides real bugs everywhere else.

#### Assignment chains in `trade_groups`

Extend §4.6 rather than adding a new concept. An assignment chain is a group with `strategy_type = ASSIGNMENT_CHAIN` linking: the original spread → the assigned leg → the resulting underlying position → its eventual disposal. Group P&L is the true economic result of the whole episode.

Two consequences for the spread metrics in §6.3:

- **`max_risk` becomes wrong the moment assignment happens.** A $2,000-wide put spread's defined risk converts into a stock position tying up far more capital. Freeze the at-open risk figure, set `assigned = true` on the group, and compute a second `post_assignment_capital` from the underlying position value. Report `return_on_risk` against both, labelled.
- **Duration stops being one number.** Report option-leg duration and underlying-holding duration separately; a combined figure is meaningless.

#### Journaling hook

Assignment is a high-information event worth capturing while it's fresh. On detection, auto-create a draft note on the chain and auto-apply an `assigned` tag, so `origin`/tag filtering can answer "how do my assigned positions actually resolve?" once there are enough of them to matter.

### 5.5 Positions with no opening execution

Two distinct causes, and they need different treatment:

**(a) Window truncation.** The position was opened before the file's date range. Fixed by importing the earlier period. Genuinely a data gap.

**(b) Transferred-in position (ACATS/ATON or internal).** No opening execution exists in this account, ever. Importing more history will not help. The sample's `SELLTOCLOSE −100 BCGX` is this case.

In both cases, do **not** silently drop the close and do **not** treat proceeds as pure profit.

#### The `CLOSED_LOT` shortcut — usually there is no orphan at all

Flex's `Lot` records (`levelOfDetail=CLOSED_LOT`) carry the **opening date and cost basis even when the opening execution falls outside the query window.** Verified: the 90-day query starting 2031-05-12 contains no BCGX purchase, yet its closed lot reports `openDateTime=20310409`, `cost=2490.388` (24.90388/share), `fifoPnlRealized=66.33080756`.

**So for anything closed in-window, the orphan problem does not arise** — the lot record supplies the missing side. Synthesize the opening leg from it (`synthetic = true`), and reserve the manual-basis path below for positions still open whose acquisition predates all available data.

#### Detection and enrichment

Flex's **Transfers (ACATS, Internal)** section identifies case (b) automatically. Per the field reference it carries Conid, Symbol, Date, Type (ACATS, Internal, etc.), Direction (In or out), Quantity, Transfer Price, Position Amount (the market value of the position), P/L Amount (the realized P/L associated with the transfer), and Transaction ID. Match on `conid` + `date` and you have everything needed to synthesize the opening leg — **no manual entry required once Flex sync is live** (§3.9: add this section to the query).

Manual entry remains available for pre-Flex history and for correcting a transfer where IBKR's basis is wrong or absent.

> **ACATS basis caveat — confirmed live.** The BCGX transfer record reports `transferPrice=0`. The usable figures are `cost=2490.388` (the delivering broker's carried-over basis) and `positionAmount=2598.275` (market value on the transfer date, 2031-06-26). **Do not read `transferPrice`** — it is zero here and cannot be relied on. Derive per-unit basis from `cost / quantity` and the transfer mark from `positionAmount / quantity`.

#### The attribution decision (§14.3)

A transferred-in position raises a question the app must answer explicitly: **whose performance is the pre-transfer gain?**

**This is no longer hypothetical — the Flex data gives all three numbers for the same BCGX trade:**

| Basis used | Per share | Realized P&L | Where it comes from |
|---|---|---|---|
| Hand-entered opening in the edited `.tlg` | 22.9921 | **+257.02** | A reconstruction. Wrong by ~$190 |
| Delivering broker's carried-over cost (`ORIGINAL_COST`) | 24.90388 | **+66.33** | Matches IBKR's `fifoPnlRealized` exactly. The tax view |
| Market value at transfer (`TRANSFER_MARK`, default) | 25.98275 | **−41.07** before commission | The performance view: what happened while the position was here |

Same trade, three answers spanning $693 — and the sign flips. On a ten-week sample this single position would otherwise dominate win rate, best-trade, and symbol concentration.

> **IBKR agrees with the `TRANSFER_MARK` default.** `ChangeInNAV` reports `transferredPnlAdjustments = -107.887`, which is exactly `positionAmount − cost = 2598.275 − 2490.388`. IBKR backs the pre-transfer gain out of its own performance figures rather than crediting it to this account. The recommended default therefore matches the broker's published TWR methodology, and choosing `ORIGINAL_COST` will make your numbers disagree with IBKR's by construction. Surface that in the settings UI.

Provide a setting, `transfer_basis_policy`:

| Value | Opening basis used | Measures |
|---|---|---|
| `TRANSFER_MARK` **(default)** | Market value on transfer date (`Position Amount` / `Quantity`) | **Your decisions in this account** — the right default for a trading-performance tool |
| `ORIGINAL_COST` | Acquisition cost carried over from the delivering broker | Total economic result of the holding; closer to the tax view |

Store both. Show the active policy in the UI wherever a transferred trade appears, and tag such trades so they can be filtered out entirely. Under either policy the trade is flagged `origin = TRANSFERRED` and excluded from **duration** statistics — an open date inherited from another broker makes hold time meaningless.

#### Equity-curve treatment (this is the one that silently breaks things)

A transfer-in is an **external flow**, not profit. It must enter the TWR calculation as `F_t` at transfer-date market value (§7.3), exactly like a cash deposit. Omit it and the account value jumps by the position's full value with no corresponding flow, and TWR reports a large fabricated gain on the transfer date.

Record it in `cash_transactions` as type `TRANSFER_IN` / `TRANSFER_OUT` with `amount = Position Amount` and a link to the resulting position. It affects account value and TWR; it never counts as trading P&L.

#### Handling summary

1. Create the trade with `origin = TRANSFERRED` (or `ORPHAN_CLOSE` for case (a) pending backfill).
2. Synthesize the opening leg per `transfer_basis_policy`; mark it `synthetic = true` so it is visibly not a real execution.
3. Case (a) only: exclude from win rate, expectancy, and R-value until an opening basis exists; surface in the import report with a prompt to import the earlier period.
4. Both cases: include the cash flow in the equity curve; exclude from duration statistics.
5. Apply the same logic in mirror to positions still open at the end of the window, which is the normal steady state.

---

## 6. Spread / Strategy Grouping

**This is the most important departure from a naive per-leg journal, and it matters for how you actually trade.** The sample is dominated by vertical put spreads — ACME, MEMC, VERT, BIDX, SEMX, and MES weeklies — where the per-leg statistics are actively misleading. `ACME 310813P00172000` shows −$61.37 and `ACME 310813P00180600` shows +$103.75; neither number means anything alone. The spread made **+$42.37**.

### 6.1 Combo detection — `brokerageOrderID` (verified)

**Primary key: `brokerageOrderID`.** Legs submitted as one combo order share it; `ibOrderID` does **not** — each leg gets its own. Verified against the Flex XML: 43 distinct `brokerageOrderID` values produced **22 two-leg groups, every one a correctly paired vertical** (ACME, MEMC, OMNI, VERT, BIDX, SEMX, and the MES futures-option spreads), plus 20 single-leg groups and 14 rows with the field empty.

Example — `ACME 13AUG31 172/180.6 P`, both legs on `0040916b.000104f6.6a7168ec.0002`, while their `ibOrderID`s are `5488585286` and `5488601377`.

This is deterministic, so use it instead of inferring. Group by `(brokerageOrderID, openCloseIndicator)`; an opening combo and its closing combo are separate orders and are re-associated by the per-instrument position matching in §5.2, not here.

**Fallback: exact timestamp.** Needed for the 14 empty-field rows, which split into two kinds:

- **10 expirations** — `transactionType=BookTrade`, `notes=Ep`, `exchange=--`, price 0. These have no order because no order was placed. Match them to their position, not to each other.
- **4 real exchange fills** — the VERT `17JUN32 35.475/39.775` opening pair and the MES `01JUL31 3083.1/3126.1` opening pair. Genuine combos with no `brokerageOrderID` recorded, so the timestamp heuristic below still earns its place.

When falling back, require **all** of: same `underlying_symbol`, exact matching `dateTime`, asset class in `{OPT, FOP}`, and opposing directions. **Do not relax the timestamp match to a date match** — the two unrelated MEMC naked puts opened 2031-07-14 at `10:21:44` and `12:40:43` would fuse into a fictitious spread (test 56).

`.tlg` files carry no order identifier of either kind, so `.tlg`-sourced data uses the timestamp fallback throughout. This is one more reason Flex is the primary path.

### 6.2 Strategy classification

| Legs | Pattern | Type |
|---|---|---|
| 2 | Same expiry, same right, one long / one short, different strikes | Vertical (Debit/Credit, Call/Put) |
| 2 | Same strike & right, different expiries | Calendar |
| 2 | Different strike & expiry, same right | Diagonal |
| 2 | Same expiry, one call + one put, both long / both short | Straddle (same strike) / Strangle |
| 3 | Same expiry & right, 1:2:1 ratio | Butterfly |
| 4 | Same expiry, two verticals opposite rights | Iron condor / iron butterfly |
| 4 | Same expiry & right, two verticals | Condor |
| 1 + stock | Short call + 100 long shares | Covered call |
| **1** | **Single short put, no offsetting long** | **`NAKED_SHORT_PUT`** |
| **1** | **Single short call, no offsetting long** | **`NAKED_SHORT_CALL`** |
| **1** | **Single long option** | **`LONG_SINGLE`** |
| n | Anything else | `CUSTOM` |

**Single-leg positions are first-class, not leftovers.** They are the primary strategy here — naked short puts on MES futures options, with equity spreads as a capital-constrained substitute. A single-leg short must classify as `NAKED_SHORT_PUT`, never fall through to `CUSTOM`, and never be force-paired with an unrelated position.

The exact-timestamp rule in §6.1 is what makes this work. On 2031-07-14 the account opened a MEMC naked put at `10:21:44` and a second MEMC naked put at `12:40:43`. A looser same-day-same-underlying heuristic would fabricate a spread from two unrelated positions. **Do not relax the timestamp match to a date match** — the fixture will catch it (test 56).

### 6.3 Group-level metrics

- **Net debit/credit at open** — from the sample's `ACME 13AUG31 172/180.6 P`: paid 0.7181, received 1.3158 → **net credit 0.5977** ($59.77)
- **Capital at risk** — by strategy type:

  | Type | `max_risk` |
  |---|---|
  | Credit vertical | `(strike width − net credit) × multiplier` |
  | Debit vertical | net debit paid |
  | **Naked short put** | `(strike × multiplier) − credit received` — the cash-secured equivalent, i.e. the loss if the underlying goes to zero |
  | **Naked short call** | Unbounded. Store `null`; **never** substitute a finite placeholder |
  | Long single | premium paid |

- **Return on risk** = `net_pnl / max_risk`

> **Do not pool return-on-risk across strategy types.** An MES 7120 naked put carries roughly `7120 × 5 = $35,600` of cash-secured risk; an ACME 400/420 vertical carries `$2,000 − credit`. Averaging the two produces a number that means nothing, and it will look plausible. Segment every risk-normalized statistic by `strategy_type`, and make the app refuse to render a pooled figure rather than rendering a misleading one.
>
> `max_risk = null` (naked short calls) must **propagate as null**, never as zero and never dropped from the denominator. A silently excluded position inflates the average.

- **Optional: actual margin.** The cash-secured figure above is conservative and derivable from the data; IB's actual SPAN requirement on a naked MES put is far lower and is *not* in the Flex trades data. Provide a manual `margin_requirement` field per group, and report `return_on_margin` alongside `return_on_risk` when populated. Which denominator matters depends on the question — capital efficiency versus worst-case exposure — so show both rather than picking.
- **Group win rate, expectancy, average hold time** — computed on groups, not legs
- **DTE at open**, **% of max profit captured** (credit spreads), **breakeven**

### 6.4 UI requirements

- Global toggle: **Analyze by Leg / Analyze by Strategy**. Every chart, stat, and table respects it.
- Trades table collapses grouped legs into one parent row, expandable to legs.
- Manual override: select N trades → "Group as strategy" → pick type. Also ungroup. Persist `user_confirmed` so a rebuild never clobbers a manual grouping.
- Strategy-type becomes a first-class grouping dimension for every chart (§9).

---

### 6.5 Books: separating unrelated strategies

Long-term stock holdings and options income are **different strategies that must not be pooled**. In the fixture, BCGX's `+$257.02` from a buy-and-hold position exceeds the net result of nearly every individual options position. Pooled into one win rate, expectancy, or average-P&L figure, it silently dominates conclusions about how the options strategy is performing.

Add `book` as a first-class field on `trades` and `trade_groups` — a mutually exclusive partition, distinct from tags (which are many-to-many and descriptive).

**Auto-assignment rules**, user-editable, applied at trade construction:

| Rule | Book |
|---|---|
| Asset class `OPT` or `FOP` | `Options Income` |
| Asset class `STK` or `FUT` not arising from an assignment chain | `Holdings` |
| Underlying position created by assignment (§5.4) | Inherits the book of the option that was assigned |

That last rule matters: an assigned MES put becomes a futures position, but it is the *outcome of an options trade*, not a new directional holding. It belongs with the position that created it, and the assignment chain (§5.4) keeps that linkage.

**Requirements:**

- `book` is a global filter dimension (§9.1), defaulting to `Options Income` on the statistics page rather than "all". The pooled view must be a deliberate choice.
- Every statistic in §8 respects it. Equity curve and account value (§7) always show **all** books — that is total account performance and should not be filtered.
- Show the active book in the statistics page header, next to the transfer-basis policy (§14.3). Both are settings that silently change what a number means.

### 6.6 Strategy regime changes

Strategies evolve, and statistics pooled across a change describe a portfolio that no longer exists. The fixture shows this directly: MES positions were vertical spreads through 2031-07-06, then naked short puts from 2031-07-13 onward. A single "MES win rate" spans two materially different risk profiles.

The app does not need to detect regimes automatically — that is over-engineering for one user. It needs to make them **visible and separable**:

- `strategy_type` is already a filter dimension (§9.1); ensure it is filterable at the *group* level, not just per-leg.
- The trend-analysis chart (§9.4) plots any metric per-trade with a moving average, which is exactly the view that reveals a regime shift.
- Optional, low cost: a user-defined `regime` marker — a dated annotation ("switched MES to naked 2031-07-13") rendered as a vertical line on every time-series chart. Cheap to build, and it turns "why did this change?" into a one-glance answer.

Given the stated plan to move from equity spreads to naked shorts as capital allows, this will happen again. Build the seam now.

**✅ BUILT.** All three mechanisms exist. `strategy_type` filters at group level through the chart grid's global filters; the trend chart (§9.4) plots any metric per-trade with SMA and EMA overlays; and `regime_markers` are dated annotations drawn as vertical lines, placed at the first trade closing on or after the date. A marker dated after the last trade is listed rather than drawn, because there is nothing yet for it to mark.

The app still does not detect regimes, deliberately. Automatic detection remains over-engineering for one user, and a marker the user wrote is better evidence than one the app inferred — it records *why*, which no detector can.

## 7. Cash Transactions and Account-Level Performance

### 7.1 Entry

**Primary source: automatic.** The Flex `CashTransactions` section (§3.9) delivers deposits, withdrawals, dividends, interest, and fees without any manual work. Map IB's `type` values onto the `cash_transactions.type` enum and flag unrecognized types as `ADJUSTMENT` with the raw string preserved in `note`.

Retain a **manual CRUD form** and CSV paste path for: pre-Flex history, corrections, and any transaction IB categorizes in a way you want to override. Fields: date, type, amount, note. Signed convention: deposits positive, withdrawals negative. Mark manually-entered rows with a `source` column so a resync never overwrites them.

The equity curve is anchored on `tracking_start_date` / `tracking_start_value` (§0.3), **not** on account opening. Without that anchor, percentage returns are meaningless — the exact problem TradesViz's docs call out for new users. Flows before the epoch are stored but excluded from the curve; the epoch value already embeds them.

### 7.2 Derived series

For each day in `daily_snapshots`:

```
cash_flow_t     = Σ deposits − Σ withdrawals on day t
account_value_t = starting_balance + Σ(cash_flow ≤ t) + Σ(realized_net ≤ t) [+ unrealized_t if available]
```

### 7.3 Return calculations

**Simple return** — `(ending_value − starting_value − net_deposits) / (starting_value + net_deposits)`. Easy, and wrong when deposits are large relative to the account.

**Time-weighted return (TWR)** — the correct measure of *trading skill*, since it strips out the timing and size of your own contributions. Chain daily sub-period returns:

```
r_t = (V_t − V_{t-1} − F_t) / (V_{t-1} + F_t)      # F_t = net external flow on day t
TWR = Π(1 + r_t) − 1
```

**Money-weighted return (IRR/XIRR)** — the return on *your actual dollars*. Solve for the rate where NPV of all flows plus terminal value equals zero. Show alongside TWR; they answer different questions and the gap between them is itself informative.

**CAGR** — `(ending/beginning)^(365/days) − 1`. Compute from the TWR series, not raw values, so deposits don't inflate it.

Show TWR as the headline number and label the others clearly. Guard against divide-by-zero when `V_{t-1} + F_t ≈ 0`.

### 7.4 Equity curve variants

Selectable overlays, matching TradesViz's approach: **P&L only** (no cash flows) · **P&L + deposits/withdrawals** · **+ dividends** · **benchmark overlay** (see §14.2).

### 7.5 Benchmark Comparison — **in scope for v1**

The question worth answering: *is this outperforming just buying the index?* Given the existing SPY-relative spread analysis, build it from the start.

#### Method: cash-flow-matched buy-and-hold

Comparing your TWR against SPY's raw period return is apples-to-oranges — SPY's return has no deposits in it. Instead, run a shadow portfolio:

```
On each external flow F_t (deposit, withdrawal, transfer-in/out at market value):
    shadow_shares += F_t / spy_adjusted_close_t      # fractional shares allowed
shadow_value_t = shadow_shares × spy_adjusted_close_t
```

Same money, same dates, passively invested. Plot `account_value_t` against `shadow_value_t` on one chart. The gap is the honest answer.

Report alongside: **alpha** and **beta** from an OLS regression of daily account returns on daily SPY returns, **correlation**, **tracking error** (stdev of the return difference), and **information ratio** (mean excess return / tracking error). All gated behind the same ≥30-day sample-size check as §8.2 — with ten weeks of data these will be noise, and the UI should say so rather than print a confident-looking beta.

#### Use total return, not price return

SPY yields roughly 1.2%. Your account P&L includes dividends and every other cash effect; SPY's *price* return does not. Benchmarking against price-only overstates your relative performance by approximately the dividend yield every year — small per year, material over three.

Use **adjusted close** (splits and dividends reinvested) or reconstruct total return from close plus dividend cash. Whichever source you pick, verify empirically that its adjusted series is dividend-adjusted and not merely split-adjusted; several free sources are ambiguous about this. A quick check: SPY adjusted-close total return for a full calendar year should exceed price return by roughly the yield.

#### Data source

The requirement is trivially small — one symbol, daily closes, incremental fetch, cached locally forever. Almost anything works; the differences are licensing and durability.

| Source | Key | Assessment |
|---|---|---|
| **Tiingo** *(recommended)* | Yes, free | Freemium with a generous free tier, cleaner and better-documented end-of-day prices, with explicit terms of service. Provides `adjClose` and `divCash` separately, so the total-return question is unambiguous. Free tier is far more than one symbol needs |
| **Stooq** *(fallback)* | No | Free daily historical prices for global stocks, indices and FX, no key, reachable through pandas-datareader; permits personal/non-commercial use. Good zero-friction backup. **Verify dividend adjustment before trusting it** |
| **yfinance / Yahoo** | No | An unofficial Python wrapper scraping an undocumented endpoint; Yahoo's ToS does not contemplate programmatic redistribution — treat the data as personal research only. Convenient for prototyping, but it breaks whenever Yahoo changes its endpoint, and the failure is silent-ish. Fine for local dev; poor foundation for a system you want working untouched in two years |
| **Alpha Vantage** | Yes, free | Free tier is 25 requests/day — ample for one symbol, tight if scope grows |
| **Twelve Data** | Yes, free | 800 requests/day on the free plan |

**Design for source-independence.** A `PriceSource` interface with one method — `daily_bars(symbol, from, to) -> [(date, close, adj_close)]` — and adapters behind it. Prices land in a `price_bars` table (`symbol, date, close, adj_close, source, fetched_at`) and are read only from there. Swapping providers then costs one adapter, and a provider outage degrades to stale cached data rather than a broken dashboard.

Fetch on the same schedule as the Flex sync (§3.8), incrementally. Failures are non-fatal: log, retry next run, show the benchmark line as stale rather than blanking the chart.

> **Not investment advice, and not a signal.** This comparison is a retrospective measurement of one account against one index over a short window. Ten weeks of outperformance or underperformance says essentially nothing about skill, and the UI should present the sample-size caveat next to the number rather than in a footnote.

---

## 8. Statistics Catalog

All computed against the **currently active global filter set**. Gross/net toggle applies globally.

### 8.1 P&L

**Totals:** total net closed P&L · total gross closed P&L · realized P&L incl. partials · unrealized P&L (Phase 4) · sum of winners · sum of losers · total account value · total deposits (+ return on deposits %) · total withdrawals · net money transactions

**Averages:** per trade (and average % return) · per day / week / month / year · same split by winning vs. losing trades

**Extremes:** best trade · worst trade · best day · worst day · largest winning streak P&L · largest losing streak P&L

**By period:** trailing day / week / month / year P&L; month-over-month and year-over-year tables

### 8.2 Ratios and scores

| Metric | Formula |
|---|---|
| Win rate | `wins / (wins + losses)` — breakevens excluded from denominator; report separately |
| Profit factor | `Σ gross profit / |Σ gross loss|` |
| Win/loss ratio | `avg winner / |avg loser|` |
| Adjusted win/loss | `(avg win × win%) / (|avg loss| × loss%)` |
| Expectancy | `(win% × avg win) − (loss% × |avg loss|)` |
| Gain-to-pain | `Σ all returns / |Σ negative returns|` |
| Kelly criterion | `win% − (loss% / (avg win / |avg loss|))` |
| SQN (Van Tharp) | `(mean trade P&L / stdev trade P&L) × √n`, n capped at 100 |
| Sharpe | `(mean daily return − rf_daily) / stdev daily return × √252` |
| Sortino | same, denominator = stdev of *negative* daily returns only |
| Calmar | `annualized return / |max drawdown %|` |
| Omega | `Σ positive daily P&L / |Σ negative daily P&L|` |
| Recovery factor | `net profit / |max drawdown|` |
| Ulcer Index | `√(mean(drawdown%²))`; UPI = `(return − rf) / UI` |
| Tail ratio | `P95(daily P&L) / |P5(daily P&L)|` |
| R-value | `net_pnl / initial_risk`, where risk = `|entry − stop| × qty × multiplier` |
| CAGR | §7.3 |

Sharpe/Sortino/Omega/Calmar require **≥30 trading days** of data to be meaningful — gate them behind a sample-size check and show "insufficient data" rather than a garbage number. With ~10 weeks of history in the sample file, most of these will be noise; say so in the UI.

### 8.3 Risk and consistency

Max drawdown (absolute and %, with peak/trough dates and recovery duration) · current drawdown · P&L standard deviation (all / daily / winners-only / losers-only) · max consecutive wins and losses with date ranges · longest flat period

### 8.4 Trades, volume, duration, costs

**Trades:** total · winning · losing · breakeven · long · short · open vs. closed · winning days vs. losing days · avg and max trades per day/month/year

**Volume:** total contracts/shares · winning vs. losing volume · P&L per unit · avg/max/min per trade and per day

**Duration:** total time in market · avg/max/min hold, split by winners and losers · intraday vs. multi-day breakdown

**Costs:** total commissions · fees · commissions as % of gross P&L · avg per trade / per day / per contract · cost drag on the equity curve. Worth a dedicated widget: the sample's $21.62 of commissions against $1,549.56 of gross flow is a meaningful slice, and per-leg spread trading multiplies it.

**Futures-specific:** P&L in points · P&L in ticks · points per contract · $/point (needs §4.2 tick reference)

**Capital efficiency (by `strategy_type`, never pooled):** premium captured per dollar of `max_risk` · premium captured per dollar of `margin_requirement` where entered · average `max_risk` per position · peak concurrent `max_risk` across open positions. Given the stated intent to shift from equity spreads to naked shorts as capital allows, this is the comparison the app exists to inform — but note it measures realized outcomes over a short window, not the risk actually taken. A naked short put and a vertical can show similar returns right up until the tail event that distinguishes them, and no statistic computed from ten weeks of winners will tell you that.

### 8.5 Symbol / underlying

Best and worst by total P&L · by win rate · most and least traded by volume and by count · highest single winner and loser · P&L concentration (what % of net P&L comes from the top 3 symbols — a genuinely useful risk flag)

---

## 9. Charts

Every chart: click a data point to drill into the underlying trades; right-click to apply that bucket as a global filter. This interaction model is what makes TradesViz's chart count useful rather than overwhelming — build the interaction once, generically.

### 9.1 Grouping dimensions (the x-axis library)

Time of day · day of week · day of month · month · year · trade duration bucket · hold-time bucket · symbol · underlying · asset class · strategy type (§6) · side (long/short) · position size bucket · price range bucket · DTE-at-open bucket · tag · tag group · account

### 9.2 Metric families (the y-axis library)

Net P&L · gross P&L · win rate · trade count · volume · avg P&L per trade · expectancy · profit factor · R-value · commissions · avg duration · return on risk

**Any metric × any dimension** = the chart grid. Implementing this as a single parameterized component gives you ~200 charts from one piece of code, which is exactly how TradesViz gets its numbers up. Ship a curated default set (below) and let the axes be swapped from a dropdown.

### 9.3 Default dashboard

1. Cumulative net P&L curve (with drawdown shaded beneath)
2. Account equity curve incl. deposits (§7.4)
3. Monthly P&L calendar heatmap → click a day to drill in
4. P&L by day of week
5. P&L by time of day
6. Win rate by strategy type
7. P&L distribution histogram (with a normal overlay)
8. P&L by underlying (top/bottom 10)
9. Rolling 20-trade expectancy and win rate
10. Commissions per month vs. gross P&L
11. Hold duration vs. P&L scatter (each dot a trade; green win, red loss)
12. DTE-at-open vs. return-on-risk (options-specific, high signal for spread trading)

### 9.4 Additional views

- **Drawdown chart** — underwater equity plot with recovery periods marked
- **Distribution curves** — P&L, % return, R-value, return-on-risk
- **Trend analysis** — any metric plotted per-trade (not per-date) with an SMA/EMA overlay; answers "is my edge improving over the last 50 trades?"
- **Treemap** — P&L by symbol / strategy / tag, sized by volume
- **Streak chart** — rolling win/loss score (+1 win, −1 loss)
- **Scatter compare** — any metric vs. any metric, bubble-sized by a third

---

### 9.5 Expiry Watch — the one forward-looking view

Everything else in this app is retrospective. This panel is the deliberate exception, and it exists because European-style exercise makes the outcome **schedulable**: the date is known, ITM auto-exercises, and no abandonment is allowed. A naked MES put that finishes ITM becomes a long futures position whether or not anyone is watching. The panel turns a Monday-morning discovery into a Friday-afternoon heads-up.

Cheap to build — it needs only open positions and instrument metadata, both present after Phase 1.

#### Content

One row per open option position (and per group, collapsible), sorted by expiry ascending:

| Column | Notes |
|---|---|
| Underlying / symbol | |
| Strategy type + book | From §6.2 / §6.5 |
| Quantity, strike(s), right | |
| Expiry date, **DTE** | Calendar and trading days both; a 2-DTE position over a holiday weekend is not a 2-day position |
| Exercise style | `EUROPEAN` / `AMERICAN` / `UNKNOWN` — drives the two behaviours below |
| Underlying reference price + as-of timestamp | See caveat |
| Distance to strike (points and %) | Signed; negative = ITM |
| **If assigned: resulting position** | The whole point of the panel — see table below |
| Group `max_risk` | From §6.3; `null` renders as "unbounded", never blank |

#### "If assigned" projection

| Position | Result | Example from the fixture |
|---|---|---|
| Naked short put on MES future | Long *n* MES futures at strike | `MES 15AUG31 3074.5 P` × 1 → long 1 MES at 7150, **~$35,750 notional** |
| Naked short put on stock | Long *n* × 100 shares at strike, cash required | `MEMC 17JUN32 12.47 P` × 1 → long 100 MEMC, **$2,900 cash** |
| Short leg of a vertical | Capped at group `max_risk`; show both legs' fates | |
| Long option ITM at expiry | Auto-exercises into the underlying — a *long* option is not a safe no-op | |

Show notional and cash-required prominently. That number, not the DTE, is the thing worth seeing.

#### Two behaviours by exercise style

- **European** → a **scheduled** event. Surface the panel on the dashboard whenever any position expires within a configurable window (default 5 calendar days), escalating to a banner on expiry day. Nothing can happen before the date, so the alerting is calm and predictable.
- **American** → a **surprise** vector. The panel still shows these, but early assignment is detected reactively via §5.4, and that path keeps its persistent until-acknowledged banner. This is where the two naked MEMC short puts live.

#### The price caveat, stated in the UI

The reference price is an approximation and must be labelled as such:

- Equity underlyings: daily close from the §7.5 `PriceSource` — **stale by up to one day**.
- MES: there is no free direct feed for the front-month MES future. Approximate from an S&P 500 index close and label it clearly; index-to-future basis means the displayed distance-to-strike is indicative, not exact.
- If no price is available, **still render the row.** Expiry, strike, quantity, and the resulting-position projection are the operationally important fields and none of them need a price.

Render the as-of timestamp next to every price. A silently stale number here is worse than no number.

> This is a forecast of a mechanical consequence, not a recommendation. The panel states what happens if a position finishes ITM; it does not suggest rolling, closing, or holding, and it must not display anything resembling a signal or a probability of assignment. ITM is settled on a VWAP fixing the app cannot see (§5.4), so any such number would be false precision on top of stale data.

#### Closing the loop

After an expiry date passes, the next sync should produce either an `Ep` record or an assignment for every watched position. If neither appears within two business days, flag the position as **unreconciled** in the import report. This catches the case where a position quietly vanished from the data — the failure mode a forward-looking panel exists to prevent.

## 10. Tables

### 10.1 Trades grid

Virtualized, sortable, multi-column filterable, column show/hide with persisted layout, CSV/XLSX export, expandable rows revealing constituent executions.

**Columns:** symbol · underlying · asset class · strategy type · side · status · open date/time · close date/time · duration · executions · open price · close price · quantity · total volume · cost basis · gross P&L · net P&L · commissions · % return · return on risk · P&L per unit · points · ticks · R-value · stop · target · DTE at open · strikes · expiries · tags · notes · account

### 10.2 Other grids

- **Executions grid** — raw rows exactly as parsed, with source file and line. Essential for debugging import problems.
- **Symbol-grouped** — parent row per underlying, child rows the trades
- **Day-grouped** — parent row per date with daily stats, child rows the trades closed that day
- **Open positions** — current positions with cost basis, reconciled against the latest `LOT` records

### 10.3 Bulk operations

Multi-select → add/remove tags, set stop/target, group as strategy, add note, recalculate.

---

## 11. Journaling and Editing

### 11.1 Tags

Free-form with autocomplete, organized into groups (`Setup`, `Mistake`, `Market Regime`, `Emotion`). Both trade-level and day-level. Tag analysis charts mirror the standard metric × dimension grid.

### 11.2 Notes

Markdown, on trades, on days, and standalone. Full-text searchable.

### 11.3 Manual adjustments

- Stop loss / profit target per trade (unlocks R-value)
- Manual trade entry for anything not in a `.tlg`
- Corporate action adjustments (split ratio applied to historical quantities/prices for a symbol from a date)
- Opening cost basis for orphan closes (§5.5)

---

## 12. Phase 4 Hooks (Market Data)

**Flex already covers the first row below.** Open Positions with `markPrice`/`closePrice` (§3.9) gives daily marks, unrealized P&L, and true account value with no external vendor. Everything else here needs purchased intraday data.

If a data feed is added later, these unlock without schema changes:

| Feature | Data required |
|---|---|
| Unrealized P&L, open-position tracking | ~~Daily closes~~ — **already available via Flex Open Positions** |
| MFE / MAE | 1-min OHLC over each trade's holding period |
| Best exit, exit efficiency | 1-min OHLC for holding period + N days after |
| Running P&L, positive/negative P&L time | 1-min OHLC |
| Benchmark overlay (SPY) | Daily closes |
| Technical-indicator correlation | Daily OHLC + indicator library |

Realistic sources: IBKR Client Portal API (you already have the account), Polygon.io, or Databento. **Options intraday history is the expensive, hard part** — for spread trading, an approximation from the underlying's price path plus a pricing model may be more practical than paying for options tick data. Treat MFE/MAE on options as explicitly approximate if you go that route.

The cheap, high-value first step here is daily closes for open positions only — that alone gives you unrealized P&L and a complete account value, for a handful of API calls a day.

---

## 13. Technical Recommendations

Deliberately boring, because this is a personal tool that should still work in three years.

| Layer | Recommendation | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Pandas/NumPy for the metric engine; Pydantic for parse validation |
| Database | PostgreSQL 16 | Window functions for cumulative/rolling metrics; `NUMERIC` for money |
| Migrations | Alembic | |
| Frontend | React + TypeScript + Vite | |
| Charts | ECharts | Best-in-class interaction (drill, zoom, brush) at this chart count; Recharts is fine if you prefer lighter |
| Tables | TanStack Table + TanStack Virtual | Virtualization is required at 80+ columns |
| State | TanStack Query + Zustand | Filter set in Zustand, server data in Query |
| Scheduler | APScheduler in-process, or a host cron hitting an internal endpoint | Avoid Celery/Redis for one job a day |
| HTTP client | `httpx` with explicit `User-Agent`, timeouts, and `tenacity` for classified retries | §3.8 |
| Secrets | Docker secret or OS keyring | §3.10 |
| Price data | Tiingo free tier behind a `PriceSource` interface | §7.5; Stooq as keyless fallback |
| Deploy | Docker Compose, single host | |

**Money handling:** `NUMERIC(18,6)` in Postgres, `Decimal` in Python, integer minor units or a decimal library in TS. Never floats. Verify at every layer boundary — the FOP multiplier of 5.00 on MES means rounding errors compound quickly on 20+ contracts.

**Metric engine:** pure functions taking a filtered trade set and returning a stats object. No database access inside them. That makes them trivially unit-testable, which is the difference between a dashboard you trust and one you second-guess.

**Recompute strategy:** full rebuild of trades and `daily_snapshots` on every import. At personal-account volume (a few thousand executions a year) this takes under a second and eliminates an entire category of incremental-update bugs.

---

## 14. Design Notes and Decisions to Make

### 14.1 Timezone and the trading day — **decided**

Executions are broker-local. The sample's MES trades at `18:07:05` and `19:05:29` are Globex evening session — under naive UTC conversion they land on the *next* calendar day and corrupt every daily statistic. Store tz-aware, display in `America/New_York`.

**Decision:** a configurable `session_start_hour`, defaulting to **18:00 ET, applied to FUT and FOP only**. Equities and equity options use calendar days. This follows CME's session convention (18:00–17:00 next day), so a futures trade at 18:07 ET Sunday belongs to Monday's trading day.

Implementation notes:

- Apply the shift once, in `daily_snapshots` construction, and derive every daily-frequency statistic from that table. Do not re-derive the trading day in individual queries — that is how the two halves of a dashboard end up disagreeing.
- Store both `executed_at` (true timestamp) and `trading_day` (derived) on `executions`. The derived column is what every `GROUP BY` uses.
- Surface the convention in the UI: a tooltip on the calendar and daily-P&L charts reading "futures sessions begin 18:00 ET the prior day."
- DST: the 18:00 boundary is 18:00 *Eastern*, not a fixed UTC offset. Use a tz-aware library and never hardcode −04:00/−05:00.
- A mixed day (equity trades plus an evening futures entry) legitimately splits across two trading days. That is correct, not a bug — document it so it isn't "fixed" later.

### 14.2 Benchmark comparison — **decided: in for v1**

Specified in full at §7.5. Cash-flow-matched SPY buy-and-hold shadow portfolio, total-return basis, source-independent price adapter.

### 14.3 Transferred positions

`transfer_basis_policy` (§5.5) is the one setting most likely to be set wrong and never noticed, because both values produce plausible-looking numbers. Default to `TRANSFER_MARK`: the question this app exists to answer is "how well am I trading *here*," and a position that arrived by ACATS carries gains earned elsewhere under different decisions.

Make the choice visible rather than buried in settings — show a badge on any trade with `origin = TRANSFERRED`, and put the active policy in the header of the statistics page. A stat that silently includes years of someone else's holding period is worse than no stat.

Add `origin = TRANSFERRED` as a filter dimension (§9.1) so the whole dashboard can be viewed with transfers excluded in one click. That is the cleanest sanity check available: if excluding them changes the picture materially, the policy choice matters and deserves a decision rather than a default.

### 14.5 Resolved Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Multiple accounts? | **Single account for v1.** Schema keeps `account_id` throughout so multi-account is a UI problem later, not a migration. No account selector, no per-account filtering in the UI. |
| 2 | Options assignment | **In scope** (§5.4). Hasn't happened yet, but selling put spreads makes it inevitable. Build detection, linking, chain grouping, and the convention-(a) P&L treatment now — including the reconciliation carve-out. |
| 3 | Wash sales / tax lots | **Excluded.** Not a performance question, and a large separate problem. FIFO matching (§5.2) is for performance attribution only. The app is explicitly not a tax tool; say so in the UI footer. |
| 4 | Fees vs. commissions | **Now available immediately.** `UnbundledCommissionDetail` is present and populated (68 records) with `brokerExecutionCharge`, `brokerClearingCharge`, `thirdPartyRegulatoryCharge`, `regFINRATradingActivityFee`, `regSection31TransactionFee`, `regOther`. Populate `trades.commissions` and `trades.fees` from it in v1 rather than deferring — the data is already in the query. Example: BCGX sell, total −0.4912, of which −0.43 broker execution and −0.0612 regulatory. |
| 5 | Reporting Integration feed | **Not pursued** (§3.11). Flex covers nearly all of it. |
| 7 | Performance epoch | **User-configurable** `tracking_start_date` + `tracking_start_value`, fetched from IBKR with manual override (§0.3). Measurement starts when the strategy started, not when the account opened. |
| 8 | Flex token lifetime | **1 year**, with T-30 / T-7 expiry warnings (§0.6). |
| 9 | Instrument scope for reference tables | **MES now, ES later** (Appendix D). |
| 6 | Time-in-force / order type | **Not needed.** `orderType` stays unmapped even though Flex provides it. Revisit only if an execution-quality question emerges that needs it. |

### 14.6 Remaining open items

All previously open items are resolved (§14.1 session boundary, §14.2 benchmark, §14.5 the six decisions, §5.2 FIFO). The three left open for after first use have since been closed, two by building and one as far as building can take it:

1. **Chart curation — ✅ RESOLVED by making the guess non-binding.** The revisit this asked for needs usage data nobody has yet, and picking a different twelve would only be a second guess. So §9.3's set now ships as an *editable* layout: panels are added, removed and reordered from the Dashboard, and the choice is persisted. Every panel is a `(dimension, metric)` pair over §9's existing grid, so curating is a saved selection over ~266 views rather than a rewrite. `settings.dashboard_layout` being null is itself readable — it means the default has not yet been tested against use.
2. **Assignment conventions in practice — ⚠️ CANNOT be closed by code.** This needs a real assignment, and the account has never had one. What has been arranged is that the re-read happens *at* that moment rather than depending on memory: the draft note §5.4 auto-creates on a chain now carries the four conventions worth confirming — split rather than rolled P&L, frozen at-open risk, split durations, and nothing inferred — next to the figures they produced, and points back at §5.4. Every path is implemented and §16.2's tests 40–49 pass against synthetic statements. **Re-read this the first time one fires.**
3. **Sample-size gating thresholds — ✅ RESOLVED by deriving them.** The ≥30-day rule was never right *in general*, because the sample a Sharpe needs depends on the Sharpe. Every Sharpe is now reported with the standard error and 95% interval from Lo (2002), `SE(SR) = sqrt((1 + SR²/2)/n)`, plus the observation count that Sharpe would need to clear zero: `n ≥ z²(1 + SR²/2)/SR²`.

   The finding is worth recording. This account's annualized Sharpe of **3.91 over 62 observations has a 95% interval of [−0.10, 7.92] — it includes zero.** A more ordinary Sharpe of 1.0 would need roughly 970 trading days, about four years. So ≥30 days is far too permissive for the ratios it gates, and the fix is not a bigger number: it is to stop a threshold carrying the epistemics at all. The cutoff survives as a configurable display preference (`SETTLED_MIN_SAMPLE_TRADING_DAYS`) deciding only whether to *draw* a figure; the interval decides whether to believe it.

   Caveat carried in the payload: Lo's formula assumes IID returns, and trading returns are autocorrelated and fat-tailed. Both widen the true interval, so what is reported is a floor on the uncertainty rather than a bound — the safe direction to be wrong in.

## 15. Milestones

| Phase | Deliverable | Rough effort |
|---|---|---|
| **1 — Foundation** | Schema + `.tlg` parser + import pipeline + reconciliation + executions grid. Nothing but correct data. | 1–2 weeks |
| **1b — Flex sync** | Flex query setup, XML parser, two-step client with error classification, scheduler, sync-history + staleness UI, `CLOSED_LOT` reconciliation | ~1 week |
| **2 — Trades, core stats & expiry watch** | Trade construction, FIFO matching, §5.5 handling, trades grid, §8.1–8.2 statistics, cumulative P&L chart, **Expiry Watch panel (§9.5)** | 2 weeks |
| **3 — Analytics, cash & benchmark** | Chart grid (§9), global filters, calendar, cash transactions, equity curve, TWR/CAGR, **SPY benchmark overlay (§7.5)** including the price adapter and `price_bars` cache | 3 weeks |
| **4 — Spreads, assignment & journaling** | Strategy grouping + group metrics, **assignment detection / linking / chain grouping (§5.4)**, transfer handling (§5.5), tags, notes, bulk ops, manual adjustments | 2 weeks |
| **5 — Market data (optional)** | Daily closes → unrealized P&L; SPY benchmark; MFE/MAE if the data economics work | open-ended |

Phases 1 and 2 alone produce something more useful than a spreadsheet. Resist building charts before the reconciliation check passes.

---

## 16. Acceptance Tests

Two fixtures, both retained — they exercise different paths:

- **`fixture-complete.tlg`** — the current file. BCGX has a matching opening execution, so every trade is a complete round trip.
- **`fixture-orphan.tlg`** — the original file, with BCGX's opening execution absent. **Keep this one.** It is the only fixture exercising §5.5, and it reflects what production data actually looks like: BCGX was transferred in by ACATS, so no opening execution exists in the account's real history.

> **The hand-added row will not survive Flex sync.** The BCGX opening carries trade date `20310409` — outside the file's own `20310602–20310808` window, with a trade ID adjacent to the sell's and an identical timestamp and commission. It is a reconstruction, not a broker record. When the app syncs from Flex, that row will not be there, and a 30-day trailing resync (§3.8) will not reach April. **Editing broker exports by hand is not a durable fix** — the durable equivalent is the manual-basis entry in §5.5, which lives in the app's database, is marked `synthetic = true`, and survives every resync. Treat the edited file as a test fixture only.
>
> Relatedly: the parser must **not** assume trade dates fall within the filename's window. Warn when they don't — that discrepancy is a real signal, indicating either a hand-edit or an amended trade.

Values below are verified against `fixture-complete.tlg` and should be asserted in CI:

| # | Assertion | Expected |
|---|---|---|
| 1 | Sections parsed | `ACCOUNT_INFORMATION`, `STOCK_TRANSACTIONS`, `OPTION_TRANSACTIONS`, `FUTURE_OPTION_TRANSACTIONS`, `STOCK_POSITIONS`, `OPTION_POSITIONS` |
| 2 | Execution counts | 8 `STK_TRD`, 49 `OPT_TRD`, 22 `FOP_TRD` (79 total) |
| 3 | `proceeds == quantity x multiplier x price` | Holds for all 79 rows, +/-0.01 |
| 4 | Open/close code distribution | 39 `O`, 30 `C`, 10 `C;Ep` |
| 5 | Commission signs | 69 negative, 10 zero (the expiration rows) |
| 6 | `sum(-proceeds)` / `sum(commission)` | -749.65 / -22.11. Note this is **not** period P&L — it nets cash flows against positions still open at period end |
| 7 | Re-import same file | 0 new executions, 0 duplicates |
| 8 | BCGX round trip | Complete: opens 2031-04-09 at 22.9921, closes 2031-06-27 at 25.5721. Gross +258.00, commissions -0.98, **net +257.02**. Duration ~79 days |
| 8b | BCGX on `fixture-orphan.tlg` | No opening execution -> flagged for §5.5 handling; excluded from win rate and duration; cash flow still included in the equity curve |
| 9 | Open positions after import | BIDM +12; MEMC 17JUN32 12.47P -1; OMNI 21JAN33 172C +1; OMNI 21JAN33 174.15C -1. BCGX flat |
| 10 | Reconciliation vs. `LOT` records | Quantities exact; BIDM cost basis 124.94617629/share within $0.01; BCGX correctly absent from `STK_LOT` |
| 11 | Leg-level round trip | `ACME 310813P00172000` net -61.37; `ACME 310813P00180600` net +103.75 |
| 12 | Spread grouping | The above two group as one Put Credit Vertical opened 2031-08-05 10:06:11, closed 2031-08-08 13:46:46; net credit 0.5977; net P&L **+42.37** |
| 13 | Expiration handling | `ACME 25JUL31 182.75/189.2 P` closes at expiry with zero price and zero commission; group net P&L **+20.90** |
| 14 | MES spread | `MES 18JUL31 3014.3/3057.3 P` group net P&L **+1.90** |
| 15 | Trading day assignment | `FOP_TRD` at `20310706 18:12:09` assigned to the **2031-07-07** trading day per §14.1 (18:00 ET futures session start); the `STK_TRD` at `20310627 09:49:09` stays on 2031-06-27 |
| 16 | Futures options multiplier | MES options use 5.0; P&L in points = P&L / 5 |
| 16b | Out-of-window date warning | The 2031-04-09 BCGX open triggers a warning, not an error, and still imports |

### 16.1 Flex sync tests (mocked HTTP — no live calls in CI)

| # | Assertion | Expected |
|---|---|---|
| 17 | `SendRequest` omits `User-Agent` | Client raises before sending; header is non-optional |
| 18 | `v` parameter | Always `3` on both calls |
| 19 | `GetStatement` endpoint | Taken from the `<url>` in the SendRequest response, not hardcoded |
| 20 | Transient error (1005, 1009, 1019) | Retries with backoff; succeeds on a later attempt; run recorded as `SUCCESS` |
| 21 | Credential error (1012, 1015) | **Zero retries**; run recorded `CREDENTIAL_FAIL`; alert raised |
| 22 | Rate limit (1018) | Backs off; client never exceeds 1 req/sec or 10 req/min |
| 23 | Token redaction | Token appears in no log line, no error message, and no UI string |
| 24 | Staleness banner | Shown when the newest `SUCCESS` run is >2 business days old |
| 25 | Overlapping window | 30-day trailing resync produces 0 duplicate executions |
| 26 | Cross-format equivalence | The 78 shared executions from `fixture-orphan.tlg` and the Flex XML yield **identical** trades and net P&L. Asserts `Flex tradeMoney == .tlg proceeds` and `Flex proceeds == −tradeMoney`, and that `.tlg` field 1 matched to `ibOrderID` (not `tradeID`) produces 78 matches and 0 duplicates |
| 27 | `CLOSED_LOT` reconciliation | Computed per-trade realized P&L matches IB's `fifoPnlRealized` to ±$0.01, **excluding assignment chains** (see 40). Aggregate check: sum over 40 lots = **437.68**, matching both the trade-level sum and `ChangeInNAV.realized` |
| 28 | `brokerageOrderID` grouping | Produces exactly 22 two-leg groups and 20 single-leg groups; the 10 `Ep` expiration rows and 4 order-ID-less real fills route to the timestamp fallback (§6.1) |

### 16.2 Assignment and exercise tests (§5.4)

Build these against synthetic fixtures now; validate against a real assignment when one occurs.

| # | Assertion | Expected |
|---|---|---|
| 40 | Assignment chain reconciliation | Option P&L + underlying P&L under convention (a) equals IB's `fifoPnlRealized` under convention (b), ±$0.01. This is the carve-out from test 27, asserted explicitly rather than by widened tolerance |
| 41 | Short put assigned | Option closes at 0 with realized P&L = full premium; underlying opens **long** at strike, quantity = contracts × multiplier; both linked via `related_execution_id` |
| 42 | Short call assigned | Same, but underlying opens **short** |
| 43 | Long put exercised | Underlying opens **short** at strike |
| 44 | Futures option exercised | Futures position opens at strike, 1 contract per option; multiplier from the underlying future, not the option |
| 45 | Cash-settled index option | Cash adjustment at intrinsic value; **no** position created |
| 46 | Chain grouping | Spread → assigned leg → underlying → disposal form one `ASSIGNMENT_CHAIN` with `parent_group_id` set to the originating spread |
| 47 | Risk figures | `max_risk` frozen at the pre-assignment value; `assigned = true`; `post_assignment_capital` computed from underlying position value; both `return_on_risk` variants reported |
| 48 | Duration split | Option-leg and underlying-holding durations reported separately; no combined figure emitted |
| 49 | Late detection | An assignment appearing in a later statement than the option's last trading day still links correctly and surfaces in the import report |

### 16.3 Transfer handling tests

| # | Assertion | Expected |
|---|---|---|
| 34 | Flex Transfers matching | A Transfers row matching a close by `conid` + date converts `ORPHAN_CLOSE` → `TRANSFERRED` and synthesizes the opening leg |
| 35 | Synthetic legs marked | Synthesized opening legs have `execution_id = null`, `synthetic = true`, and never appear as real fills in the executions grid |
| 36 | TWR external flow | A transfer-in at market value M on day t produces **no** P&L on day t; account value rises by M; TWR is unchanged |
| 37 | Basis policy | The same transferred position under `TRANSFER_MARK` vs `ORIGINAL_COST` produces different trade P&L but **identical** account value and TWR |
| 38 | Duration exclusion | Transferred trades contribute to no duration statistic under either policy |
| 39 | Filter dimension | Filtering `origin != TRANSFERRED` removes them from every chart, stat, and table consistently |

### 16.4 Strategy classification and book tests (§6)

Verified against `fixture-complete.tlg`:

| # | Assertion | Expected |
|---|---|---|
| 55b | Cancellation pairs | The +117.7125 / −117.7125 deposit-advance pair and the ±4.30 snapshot-fee pair net to zero and do not inflate gross deposits or gross fees |
| 55c | Market-data fees segregated | The −8.84 of `Other Fees` affects account value but appears in no commission-per-trade statistic |
| 56 | No fabricated spreads | The two MEMC naked puts opened 2031-07-14 at `10:21:44` and `12:40:43` classify as **two separate `NAKED_SHORT_PUT` positions**, never as one spread. Relaxing §6.1's exact-timestamp match to a date match fails this test |
| 57 | FOP classification split | The 8 MES opening events yield **3 verticals** (2031-06-27, 06-29, 07-05) and **5 `NAKED_SHORT_PUT`** (07-12 x2, 07-19, 07-26, 08-02) |
| 58 | Equity classification | 12 two-leg verticals plus 2 naked short puts; SEMX and OMNI classify as **call** verticals, not puts |
| 59 | Naked put risk | `max_risk` for MES 08AUG31 3061.6 P = `(3061.6 x 5) - credit`, not the spread formula |
| 60 | Unbounded risk propagates | A naked short call yields `max_risk = null`; `return_on_risk` is null and the position is neither counted as zero-risk nor silently dropped from any average |
| 61 | No cross-type pooling | Requesting an aggregate `return_on_risk` across mixed `strategy_type` returns a segmented result or refuses; it never emits a single blended number |
| 62 | Book assignment | BIDM and BCGX land in `Holdings`; all OPT/FOP land in `Options Income`; the default statistics view excludes `Holdings` |
| 63 | Book contamination guard | With `Holdings` excluded, BCGX's +257.02 does not appear in options win rate, expectancy, or average P&L |
| 64 | Assigned position inherits book | A futures position created by assignment of an `Options Income` option is assigned to `Options Income`, not `Holdings` |

### 16.4b Epoch tests (§0.3)

| # | Assertion | Expected |
|---|---|---|
| 64b | Pre-epoch exclusion | Executions before `tracking_start_date` are imported and queryable but contribute to no statistic in §8 and no point on the equity curve |
| 64c | Pre-epoch position rebasing | A position open at the epoch is rebased to its epoch-date mark, flagged `origin = PRE_EPOCH`, using the same code path as `TRANSFER_MARK` |
| 64d | Straddling trades | A trade opened before and closed after the epoch is included with rebased basis, flagged `straddles_epoch`, and excluded from every duration statistic |
| 64e | Epoch change rebuilds | Changing `tracking_start_date` re-derives all trades, `daily_snapshots`, and the benchmark shadow portfolio from raw executions; running it twice with the same value is idempotent |
| 64f | Starting-value cross-check | The stored `tracking_start_value` is displayed alongside IBKR's `ChangeInNAV.startingValue` for that date, and any divergence is flagged |
| 64g | Benchmark alignment | The shadow portfolio is seeded at the epoch with `tracking_start_value`, so account and benchmark cover identical periods |
| 64h | Token expiry warning | With `token_expires_at` within 30 days a warning shows; within 7 days a persistent banner shows |

### 16.5 Expiry Watch tests (§9.5)

Against `fixture-complete.tlg`, treating the file's end date (2031-08-08) as "today":

| # | Assertion | Expected |
|---|---|---|
| 65 | Open options listed | Exactly 3 open option positions appear: `MEMC 17JUN32 12.47 P` (-1), `OMNI 21JAN33 172 C` (+1), `OMNI 21JAN33 174.15 C` (-1). No closed or expired position appears |
| 66 | Exercise style populated | The MEMC and OMNI equity options resolve to `AMERICAN`; the MES weekly/EOM series resolve to `EUROPEAN`; nothing silently defaults |
| 67 | Unknown style warns | An instrument whose series is absent from the rule table gets `exercise_style = UNKNOWN` plus an import warning, and still renders in the panel |
| 68 | Assignment projection — stock | `MEMC 17JUN32 12.47 P` (-1) projects: long 100 shares, **$1,247 cash required** |
| 69 | Assignment projection — futures | A naked `MES 15AUG31 3074.5 P` (-1) projects: long 1 MES future, **$15,372.50 notional** (strike x 5) |
| 70 | Long ITM not treated as no-op | An ITM long option projects auto-exercise into the underlying, not expiry-worthless |
| 71 | Renders without prices | With the `PriceSource` unavailable, every row still renders with expiry, DTE, strike, quantity, and projection; only the price and distance-to-strike columns show as unavailable |
| 72 | Staleness visible | Every displayed price carries an as-of timestamp; a price older than one trading day is visibly marked |
| 73 | Trading-day DTE | DTE is reported in both calendar and trading days, and correctly excludes weekends and market holidays |
| 74 | No probability output | The panel emits no assignment-probability or signal-like field under any input |
| 75 | Unreconciled expiry | A watched position whose expiry passes with neither an `Ep` record nor an assignment within two business days is flagged unreconciled in the import report |

### 16.6 Benchmark tests (§7.5)

| # | Assertion | Expected |
|---|---|---|
| 50 | Cash-flow matching | A single $10,000 deposit with no trades produces a shadow portfolio whose value tracks SPY exactly; account TWR is 0 and excess return equals the negative of SPY's return |
| 51 | Total return basis | SPY adjusted-close return over a full calendar year exceeds price-only return by approximately the dividend yield — asserts the source is dividend-adjusted, not just split-adjusted |
| 52 | Fractional shares | Shadow share count is not rounded; a $1,000 deposit at a $600 close yields 1.6667 shares |
| 53 | Source independence | Swapping the `PriceSource` adapter changes no calculation code; `price_bars` is the sole read path |
| 54 | Stale-data degradation | A price-fetch failure leaves the last cached bar in place, marks the benchmark line stale in the UI, and does not fail the sync run |
| 55 | Sample-size gate | With fewer than 30 trading days, alpha/beta/correlation/information ratio return "insufficient data" rather than a value |

---

## Appendix A — Sample File Profile

Window: 2031-06-02 to 2031-08-08 (~10 weeks), plus one reconstructed 2031-04-09 execution. 79 executions across 40 instruments.

- **Stocks:** 2 symbols. BCGX is a complete round trip in `fixture-complete.tlg` (net +257.02) but has no opening execution in `fixture-orphan.tlg` — in production it was transferred in by ACATS, so the §5.5 path is the real-world case. BIDM is a normal open position (12 shares).
- **Equity options:** 26 instruments. Twelve two-leg verticals (put verticals on ACME, MEMC, VERT, BIDX; **call** verticals on SEMX and OMNI — note not everything is a put), plus **two naked short puts on MEMC opened 2031-07-14**, one closed and one (17JUN32 12.47 P) still open.
- **Futures options:** 11 MES instruments across 8 opening events — **3 vertical put spreads (Jun 27, Jun 30, Jul 6) then 5 naked short puts (Jul 13 x2, Jul 20, Jul 27, Aug 3)**. Multiplier 5.0, evening-session entries. The switch from spreads to naked mid-window is a **strategy regime change** (§6.5) and statistics pooled across it are misleading.
- **Outright futures:** none in this window. Would appear if a naked FOP were assigned (§5.4).
- Realized outcomes are small and roughly symmetric; commissions ($22.11) are material relative to the size of individual spreads. Any statistic sensitive to sample size will be unreliable at this volume — the app should say so rather than display a confident-looking Sharpe ratio computed from ten weeks.

## Appendix B — Reference Links

- TradesViz statistics reference: `https://www.tradesviz.com/blog/general-statistics-reference/`
- TradesViz charts reference: `https://www.tradesviz.com/blog/charts-statistics-reference/`
- TradesViz deposits/withdrawals: `https://www.tradesviz.com/blog/deposits-withdrawls/`
- OCC option symbology: 6-char root + `YYMMDD` + `C|P` + 8-digit strike (3 implied decimals)
- IBKR Third-Party Reports (vendor list, context for §3.5): `https://www.ibkrguides.com/clientportal/performanceandstatements/third-party-reports.htm`
- Configure Flex Web Service: `https://www.ibkrguides.com/brokerportal/performanceandstatements/flex3.htm`
- Flex Web Service v3 error codes: `https://www.ibkrguides.com/brokerportal/performanceandstatements/flex3error.htm`
- Flex Web Service v3 (Client Portal variant): `https://www.ibkrguides.com/complianceportal/complianceportal/flexweb3.htm`
- IBKR third-party onboarding requirements (why §3.5 rules it out): `https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/`
- IBKR Reporting Integration Tool Guide (evaluated and declined, §3.11): `https://www.interactivebrokers.com/campus/ibkr-reporting/reporting-integration/`
- Reporting integration contact: `reportingintegration@interactivebrokers.com`
- Activity Flex Query field reference (all sections): `https://www.ibkrguides.com/reportingreference/reportguide/activity%20flex%20query%20reference.htm`
- Flex Transfers (ACATS, Internal) field list: `https://www.ibkrguides.com/reportingreference/reportguide/transfersfq.htm`

## Appendix C — Flex XML Validation Results

Validated against an Activity Flex Query named `Test`, `period=LastNCalendarDays` (90), `fromDate=20310512`, `toDate=20310808`, generated 2031-08-09. 78 executions — the same set as `fixture-orphan.tlg`.

### C.1 Sections returned

| Section | Count | Verdict |
|---|---|---|
| `Trade` (`EXECUTION`) | 78 | Matches `.tlg` exactly |
| `Lot` (`CLOSED_LOT`) | 40 | IB's own FIFO matching; sums to the same 437.68 realized |
| `SecurityInfo` | 39 | Full contract reference — **eliminates §2.4 parsing** |
| `UnbundledCommissionDetail` | 68 | Full fee itemization — **resolves §14.5.4 now, not v2** |
| `CashTransaction` | 12 | Deposits and fees |
| `OptionEAE` | 10 | All `Expiration`; no assignments yet |
| `OpenPosition` (`LOT`) | 4 | With `markPrice` and `fifoPnlUnrealized` |
| `Transfer` | 1 | The BCGX ACATS record |
| `ChangeInNAV` | 1 | Includes IBKR's own `twr` |
| `StatementOfFundsLine` | 200 | Complete cash ledger |
| `CorporateActions`, `TransactionTaxes` | 0 | Empty, correctly |

Every section §3.9 asked for is present and populated.

### C.2 Confirmed figures

| Item | Value |
|---|---|
| `AccountInformation.dateOpened` / `dateFunded` | `20301106` — **the performance epoch** |
| `AccountInformation.taxLotMatchingMethod` | `FIFO` — confirms §5.2 |
| Sum `fifoPnlRealized` (trades) | 437.68 — identical from `Trade` and `Lot`, a clean cross-check |
| Sum `ibCommission` | −21.62 |
| `ChangeInNAV`: starting / ending | 389.15 / 4,350.86 |
| `ChangeInNAV.twr` | 10.2478% — reference figure for §7.3 |
| `depositsWithdrawals` / `assetTransfers` | 969.65 / 2,598.28 |
| `transferredPnlAdjustments` | −107.89 (= `positionAmount − cost`) |
| Sign-convention violations | **0 / 78** |
| Distinct `transactionID` / `tradeID` | 78 / 78 |
| Orders with >1 execution | 0 (see §3.2 — do not assume this holds) |

### C.3 Corrections this file forced

1. **`.tlg` field 1 is the order ID, not an execution ID** (§2.2). Cross-format dedupe must map it to Flex `ibOrderID`, never to `tradeID`.
2. **`brokerageOrderID`, not `ibOrderID`, groups combo legs** (§6.1). 22 verticals grouped deterministically.
3. **Sign mapping pinned** (§3.9): `Flex tradeMoney == .tlg proceeds`; `Flex proceeds == −tradeMoney`.
4. **`SecurityInfo` eliminates futures-option symbol parsing** (§2.4), and `underlyingSymbol` is the specific contract `MESU1`, not the root `MES`.
5. **`CLOSED_LOT` supplies opening basis for out-of-window opens** (§5.5) — the orphan case is far rarer than assumed.
6. **BCGX's real basis is 24.90388, not the hand-entered 22.9921** (§5.5). Three defensible P&L figures span $298 and flip sign.

### C.4 Things to watch

- **Cancellation pairs.** `CashTransactions` contains `ADJUSTMENT: DEPOSIT ADVANCE` +117.7125 and `CANCELLATION` −117.7125, and a `SNAPSHOTVALUENONPRO` fee −4.30 with a matching `CANCEL[...]` +4.30. Net zero, but **naive summing double-counts them into gross deposit and fee statistics.** Detect the `CANCEL[...]` description prefix and reversal pairs, and net them before they reach any total.
- **Market-data fees are not trading costs.** `Other Fees` totals −8.84 (CME Globex, OPRA, snapshot subscriptions). These are account overhead, not per-trade commissions. Keep them out of commission-per-trade statistics; include them in account value and in a separate account-costs line. Mixing them into cost-per-trade inflates it silently.
- **`transferPrice` is 0** on the ACATS record and must not be read (§5.5).
- **`settlementPolicyMethod` is empty** in `SecurityInfo`, so exercise style still needs the static table (§0.5).
- **The 90-day window excludes the BCGX purchase** (2031-04-09), exactly as §16 predicted. Backfill to the 2030-11-06 epoch in ≤365-day chunks.
- **`ChangeInNAV.twr` is window-scoped**, not inception-to-date. Compare like with like when validating §7.3.

---


## Appendix D — Contract Reference: MES and ES

Seeds the `instruments` tick-size table (§4.2) and the `exercise_style` table (§5.4). Confidence is marked per row; anything not **Confirmed** should be verified against CME before it drives a number that matters.

### D.1 Futures

| | **MES** (Micro E-mini S&P 500) | **ES** (E-mini S&P 500) | Confidence |
|---|---|---|---|
| Multiplier | **$5 × S&P 500 Index** | **$50 × S&P 500 Index** | Confirmed — the Micro E-mini S&P 500 futures contract is $5 x the S&P 500 Index; the ES contract size is $50 times the current S&P 500 Index value |
| Tick size | 0.25 index points | 0.25 index points | Confirmed |
| Tick value | **$1.25** | **$12.50** | Confirmed — the minimum tick for ES is 0.25 index points, which equals $12.50 per contract; for MES the same 0.25-point tick is worth $1.25 |
| Point value | $5.00 | $50.00 | Confirmed |
| Settlement | Cash | Cash | Confirmed — cash-settled; no physical delivery of shares, any open position at expiration is settled in cash against the final settlement value |
| Expiry cycle | Quarterly Mar/Jun/Sep/Dec | Quarterly Mar/Jun/Sep/Dec | Confirmed — contracts expire quarterly on the third Friday of March, June, September, and December (months H, M, U, Z) |

`SecurityInfo` already supplies the multiplier per instrument (verified `multiplier=5` for MES options), so **only tick size genuinely needs this table** — it drives the points/ticks statistics in §8.4.

### D.2 Options — exercise style

| Series | Style | Early assignment | Confidence |
|---|---|---|---|
| **MES** Monday–Friday weeklies | **European** | No | Confirmed — Monday-Friday Weekly Options and EOM Options: European |
| **MES** end-of-month (EOM) | **European** | No | Confirmed — same source |
| **MES** quarterly AM-expiry | **American** | Yes | Confirmed — Quarterly AM Expiry Options: American |
| **ES** weeklies / EOM | European | No | **Inferred** — exercise procedures for options on Micro E-mini futures will mimic their E-mini options counterparts. Verify on CME's ES options spec page before trading ES |
| **ES** quarterly AM-expiry | American | Yes | **Inferred**, same basis |

Every instrument in the current fixture is an MES weekly or EOM → **European, no early assignment**.

### D.3 Options — tick size

| Series | Premium ≤ 5.00 pts | Premium > 5.00 pts | Confidence |
|---|---|---|---|
| **ES** options | 0.05 pts = **$2.50** | 0.25 pts = **$12.50** | Confirmed — 0.25 index points = $12.50 for premium > 5.00; 0.05 index points = $2.50 for premium ≤ 5.00 |
| **MES** options | 0.05 pts = **$0.25** | 0.25 pts = **$1.25** | **Inferred** by 1/10 scaling. CME confirms only the structure: the minimum price fluctuation, or tick increment, changes depending on whether the option premium is priced above or below 5.00 index points, and that Micro E-mini S&P 500 options carry a $5 per index point multiplier, 1/10 of the classic E-mini $50 multiplier |

Note the **two-tier tick** — a single `tick_size` column cannot represent it. Model as `tick_size_low` / `tick_size_high` / `tick_threshold` (5.00), or store a small rule object. Options tick size only affects presentation of P&L-in-ticks (§8.4), never P&L in dollars, so an error here is cosmetic rather than corrupting.

### D.4 Settlement semantics for MES options — matters for §5.4

CME describes these options as financially settled while also stating that option exercise results in a position in the underlying cash-settled futures contract and that in-the-money options auto-exercise into underlying futures, with no abandonment allowed.

These reconcile as: **the option exercises into a futures position; that futures contract is itself cash-settled at its own expiry.** So the §5.4 assignment projection is correct — a naked short MES put finishing ITM produces a **long MES futures position**, not a cash adjustment.

The exception is an option expiring on the same date as its underlying future (the quarterly), where the two collapse and the outcome is effectively cash. Handle by checking whether the option's expiry equals the underlying contract's expiry; if so, project a cash settlement instead of a position. Not currently reachable — every MES option in the fixture has `underlyingSymbol = MESU1`, a September contract, with option expiries in June through August.

---

