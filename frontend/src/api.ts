export interface ClosedLotCheck {
  passed: boolean
  trades_checked: number
  trades_matched: number
  lots_total: number
  ib_total_realized: number
  computed_total_checked: number
  ib_total_checked: number
  aggregate_difference: number
  skipped_assignment_chains: number
  skipped_transfers: number
  transfer_notes: string[]
  issues: string[]
}

export interface ImportReport {
  import_file_id: number
  account_id: string
  status: string
  source_format: string
  rows_parsed: Record<string, number>
  rows_inserted: number
  rows_duplicate: number
  date_range: [string, string] | null
  warnings: string[]
  reconciliation: { passed: boolean; issues: string[] }
  closed_lot_check: ClosedLotCheck | null
  assignment_alert: AssignmentAlert | null
  assignment_check: {
    chains_checked: number
    chains_matched: number
    computed_total: number
    ib_total: number
    difference: number
    passed: boolean
    issues: string[]
    method: string
  } | null
}

export interface Execution {
  id: number
  broker_trade_id: string
  symbol: string
  asset_class: string
  action: string
  flags: string[]
  executed_at: string
  trading_day: string
  quantity: number
  multiplier: number
  price: number
  proceeds: number
  commission: number
  cash_flow: number
  exchange: string
  out_of_window_warning: boolean
  related_execution_id: number | null
  source: string
  split_factor: number
}

export interface Trade {
  id: number
  symbol: string
  underlying: string | null
  asset_class: string
  side: string
  status: string
  origin: string
  book: string | null
  strategy_type: string | null
  opened_at: string
  closed_at: string | null
  duration_seconds: number | null
  open_qty: number
  avg_open_price: number | null
  avg_close_price: number | null
  gross_pnl: number
  commissions: number
  net_pnl: number
  return_pct: number | null
  pnl_points: number | null
  pnl_ticks: number | null
  excluded_from_win_rate: boolean
  excluded_from_duration: boolean
  execution_count: number
  has_synthetic_leg: boolean
}

export interface TradeGroupExtras {
  assigned: boolean
  parent_group_id: number | null
  post_assignment_capital: number | null
  return_on_post_assignment_capital: number | null
  option_duration_seconds: number | null
  underlying_duration_seconds: number | null
}

export interface GroupLeg {
  trade_id: number
  symbol: string
  side: string
  strike: number | null
  right: string | null
  net_pnl: number
}

export interface TradeGroup {
  id: number
  strategy_type: string
  book: string | null
  underlying: string | null
  opened_at: string
  closed_at: string | null
  leg_count: number
  dte_at_open: number | null
  net_debit_credit: number
  max_risk: number | null
  gross_pnl: number
  commissions: number
  net_pnl: number
  return_on_risk: number | null
  assigned: boolean
  parent_group_id: number | null
  post_assignment_capital: number | null
  return_on_post_assignment_capital: number | null
  option_duration_seconds: number | null
  underlying_duration_seconds: number | null
  detection_source: string
  legs: GroupLeg[]
}

export interface RiskSegment {
  strategy_type: string
  group_count: number
  net_pnl: number
  total_max_risk: number | null
  return_on_risk: number | null
  unbounded_risk_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
}

export interface RiskStats {
  segments: RiskSegment[]
  pooled_return_on_risk: null
  refusal_reason: string
}

export interface SyncHealth {
  last_success_at: string | null
  last_attempt_at: string | null
  last_status: string | null
  last_error_code: string | null
  last_error_message: string | null
  business_days_since_success: number | null
  is_stale: boolean
  needs_attention: boolean
  staleness_threshold_business_days: number
  scheduler_running: boolean
  next_run_at: string | null
}

export interface Settings {
  transfer_basis_policy: string
  analysis_mode: string
  default_book: string
  benchmark_symbol: string
  tracking_start_date: string | null
  tracking_start_value: number | null
  tracking_start_value_source: string
  tracking_start_value_ibkr: number | null
  price_source: string
  price_fetch_enabled: boolean
  transfer_basis_note: string
  epoch_note: string
}

/** §0.3 — the performance epoch. `configured` false means every percentage
 *  measure is withheld rather than computed against an assumed zero. */
export interface Epoch {
  tracking_start_date: string | null
  tracking_start_value: number | null
  tracking_start_value_ibkr: number | null
  tracking_start_value_source?: string
  divergence: number | null
  configured: boolean
  note: string
  rebuilt?: boolean
  rebuild_note?: string
}

export interface PerformancePoint {
  trading_day: string
  value: number
  realized_net: number
  realized_partial_net: number
  cumulative_net_pnl: number
  cash_flow: number
  income_cash: number
  unrealized_pnl: number
  unrealized_priced: boolean
  account_value: number
  drawdown: number
  drawdown_pct: number
  twr_cumulative: number | null
  benchmark_shadow_value: number | null
  benchmark_stale: boolean
  trade_count: number
}

export interface Returns {
  available: boolean
  reason: string | null
  starting_value?: number
  ending_value?: number
  net_deposits?: number
  realized_from_partials?: number
  marked_days?: number
  closing_unrealized?: number
  span_days?: number
  excludes_unrealized?: boolean
  unrealized_note?: string
  twr: number | null
  twr_note?: string
  xirr: number | null
  xirr_note?: string
  cagr: number | null
  cagr_note?: string
  simple_return: number | null
  simple_return_note?: string
  basis_variant?: string
  basis_note?: string
}

export interface BenchmarkComparison {
  symbol: string
  priced_days: number
  stale_days: number
  latest_priced_day: string | null
  is_stale: boolean
  coverage_note: string | null
  method: string
  insufficient_sample: boolean
  sample_trading_days: number
  min_sample_trading_days: number
  alpha: number | null
  alpha_annualized: number | null
  beta: number | null
  correlation: number | null
  tracking_error: number | null
  tracking_error_annualized: number | null
  information_ratio: number | null
  account_return: number | null
  benchmark_return: number | null
  excess_return: number | null
  account_final: number | null
  benchmark_final: number | null
  value_gap: number | null
  note: string
}

export interface Performance {
  account_id: string
  variant: string
  epoch: Epoch
  series: PerformancePoint[]
  returns: Returns
  benchmark: BenchmarkComparison
  ratios: AccountRatios
}

export interface CashTransaction {
  id: number
  occurred_on: string
  type: string
  amount: number
  currency: string
  source: string
  broker_transaction_id: string | null
  note: string | null
  is_external_flow: boolean
}

export interface CashList {
  total: number
  totals_by_type: Record<string, number>
  net_external_flow: number
  items: CashTransaction[]
  note: string
}

export interface ChartBucket {
  key: string
  label: string
  value: number | null
  unit_count: number
  net_pnl: number
  unit_ids: number[]
  thin: boolean
}

export interface Chart {
  dimension: string
  dimension_label: string
  metric: string
  metric_label: string
  metric_unit: string
  metric_signed: boolean
  unit_count: number
  bucket_count: number
  unplaced_units: number
  unplaced_note: string | null
  truncated_buckets: number
  thin_bucket_threshold: number
  overlapping: boolean
  overlapping_note: string | null
  buckets: ChartBucket[]
  analysis_mode: string
  filters: Record<string, unknown>
}

export interface AxisCatalog {
  dimensions: { key: string; label: string }[]
  metrics: { key: string; label: string; unit: string; signed: boolean }[]
  unavailable: { key: string; reason: string }[]
}

export interface FilterOptions {
  books: string[]
  symbols: string[]
  underlyings: string[]
  asset_classes: string[]
  strategy_types: string[]
  sides: string[]
  date_from: string | null
  date_to: string | null
}

export interface CalendarDay {
  trading_day: string
  realized_net: number
  realized_gross: number
  commissions: number
  trade_count: number
  win_count: number
  loss_count: number
  cash_flow: number
  account_value: number
  drawdown: number
}

export interface CalendarBody {
  days: CalendarDay[]
  months: { month: string; realized_net: number; trade_count: number; winning_days: number; losing_days: number }[]
  scale: { max_gain: number; max_loss: number; active_days: number; flat_days: number }
}

/** §9 — the global filter set, held in one place because a chart whose
 *  filters are invisible is a chart that gets misread. */
export interface GlobalFilters {
  date_from?: string
  date_to?: string
  books?: string[]
  underlyings?: string[]
  asset_classes?: string[]
  strategy_types?: string[]
  sides?: string[]
}

export function filterParams(filters: GlobalFilters): Record<string, string> {
  const out: Record<string, string> = {}
  if (filters.date_from) out.date_from = filters.date_from
  if (filters.date_to) out.date_to = filters.date_to
  for (const key of ['books', 'underlyings', 'asset_classes', 'strategy_types', 'sides'] as const) {
    const value = filters[key]
    if (value && value.length) out[key] = value.join(',')
  }
  return out
}

export interface Stats {
  account_id: string
  book: string
  analysis_mode: string
  analysis_mode_note: string
  unit_count: number
  pnl: Record<string, any>
  ratios: Record<string, any>
}

export interface EquityPoint {
  trading_day: string
  realized_net: number
  cumulative_net_pnl: number
  trade_count: number
  win_count: number
  loss_count: number
}

export interface OptionEvent {
  id: number
  event_type: string
  raw_type: string
  occurred_on: string
  symbol: string | null
  strike: number | null
  right: string | null
  settlement: string | null
  quantity: number
  realized_pnl_ib: number
  underlying_symbol: string | null
  option_execution_id: number | null
  linked: boolean
  underlying: {
    execution_id: number
    symbol: string
    quantity: number
    price: number
    side: string
    trading_day: string
  } | null
  link_note: string | null
  trade_group_id: number | null
  acknowledged: boolean
  needs_attention: boolean
}

export interface AssignmentAlert {
  needs_attention: number
  linking: Record<string, number>
  items: {
    id: number
    event_type: string
    occurred_on: string
    symbol: string | null
    underlying: string | null
    quantity: number
    linked: boolean
    link_note: string | null
  }[]
  note: string | null
}

export interface Tag {
  id: number
  name: string
  group_name: string | null
  color: string | null
  system: boolean
  usage_count: number | null
}

export interface Note {
  id: number
  scope: string
  scope_ref: string | null
  body_md: string
  draft: boolean
  created_at: string | null
  updated_at: string | null
}

export interface CorporateActionRow {
  id: number
  occurred_on: string
  symbol: string | null
  action_type: string
  description: string
  split_ratio: number | null
  applied: boolean
  source: string
}

export interface AccountRatios {
  basis: string
  insufficient_sample: boolean
  sample_trading_days: number
  min_sample_trading_days: number
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  omega: number | null
  ulcer_index: number | null
  max_drawdown_pct: number | null
  annualized_return: number | null
  annualized_volatility: number | null
  sharpe_significance: SharpeSignificance
  note: string
}

export interface RegimeMarker {
  id: number
  occurred_on: string
  label: string
  note: string | null
  color: string | null
  created_at: string | null
}

export interface TrendPoint {
  sequence: number
  unit_id: number
  unit_kind: string
  label: string
  trading_day: string | null
  value: number | null
  sma: number | null
  ema: number | null
  cumulative: number | null
}

export interface Trend {
  metric: string
  metric_label: string
  metric_unit: string
  window: number
  unit_count: number
  undefined_units: number
  axis: string
  axis_note: string
  window_note: string
  points: TrendPoint[]
  analysis_mode: string
  regime_markers: RegimeMarker[]
}

export interface Distribution {
  metric: string
  metric_label: string
  metric_unit: string
  count: number
  mean?: number
  stdev?: number
  bins: { from: number; to: number; centre: number; count: number }[]
  normal: (number | null)[]
  insufficient: boolean
  note: string
}

export interface DashboardPanel {
  key: string
  title: string
  kind: string
  dimension: string | null
  metric: string | null
  limit: number | null
  window: number | null
}

export interface DashboardLayout {
  panels: DashboardPanel[]
  customized: boolean
  available_special: { key: string; label: string }[]
  note: string
}

export interface SharpeSignificance {
  sharpe: number | null
  standard_error: number | null
  confidence_95: [number, number] | null
  significant_at_95: boolean | null
  observations: number
  observations_needed_95: number | null
  note: string
  method?: string
}

export interface ExpiryRow {
  instrument_id: number
  symbol: string
  underlying: string | null
  asset_class: string
  book: string
  quantity: number
  strike: number | null
  right: string | null
  expiry: string | null
  dte_calendar: number | null
  dte_trading: number | null
  exercise_style: string
  projection: string
  projection_notional: number | null
  projection_cash_required: number | null
  reference_price: number | null
  reference_price_as_of: string | null
  reference_price_source: string | null
  distance_to_strike: number | null
  distance_to_strike_pct: number | null
  moneyness: string | null
  warnings: string[]
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body}`)
  }
  return res.json()
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    // FastAPI puts the message in `detail`; showing the raw JSON envelope
    // would bury a perfectly good explanation inside braces.
    //
    // But not every error body is JSON. An unhandled exception returns the
    // plain text "Internal Server Error", and a proxy can return HTML — in
    // which case the earlier version of this rethrew `JSON.parse`'s own
    // complaint ("Unexpected token 'I'…"), a message about the parse
    // attempt that mentions neither the status nor the endpoint. Parse
    // failures here are expected; the response is what the reader needs.
    const text = await res.text()
    let detail: string | undefined
    try {
      detail = JSON.parse(text)?.detail
    } catch {
      detail = undefined
    }
    throw new Error(detail ?? `${res.status}: ${text}`)
  }
  return res.json()
}

const query = (params: Record<string, string | undefined>): string => {
  const usable = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  return usable.length ? `?${new URLSearchParams(usable as [string, string][])}` : ''
}

export const api = {
  upload: async (file: File): Promise<ImportReport> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/import', { method: 'POST', body: form })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  },
  settings: () => get<Settings>('/settings'),
  groups: () => get<{ total: number; items: TradeGroup[] }>('/groups'),
  riskStats: (book?: string) =>
    get<RiskStats>(`/stats/risk${book ? `?book=${encodeURIComponent(book)}` : ''}`),
  syncHealth: () => get<SyncHealth>('/sync/health'),
  setTransferBasisPolicy: async (policy: string) => {
    const res = await fetch(`/api/settings/transfer-basis-policy?policy=${policy}`, { method: 'PUT' })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  },
  executions: () => get<{ total: number; items: Execution[] }>('/executions'),
  trades: () =>
    get<{ total: number; transfer_basis_policy: string; items: Trade[] }>('/trades'),
  stats: (book: string, mode?: string) =>
    get<Stats>(`/stats?book=${encodeURIComponent(book)}${mode ? `&mode=${mode}` : ''}`),
  setAnalysisMode: async (mode: string) => {
    const res = await fetch(`/api/settings/analysis-mode?mode=${mode}`, { method: 'PUT' })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  },
  createGroupOverride: async (body: {
    trade_ids: number[]
    strategy_type?: string
    ungroup?: boolean
    note?: string
  }) => {
    const res = await fetch('/api/groups/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  },
  equityCurve: () => get<{ items: EquityPoint[] }>('/equity-curve'),
  expiryWatch: (asOf?: string) =>
    get<{ as_of: string; disclaimer: string; items: ExpiryRow[] }>(
      `/expiry-watch${asOf ? `?as_of=${asOf}` : ''}`,
    ),

  // §7 — account-level performance
  performance: (variant?: string) => get<Performance>(`/performance${query({ variant })}`),
  refreshBenchmark: () => send<Record<string, unknown>>('POST', '/performance/benchmark/refresh'),

  // §0.3 — the epoch
  epoch: () => get<Epoch>('/settings/epoch'),
  setEpoch: (body: { tracking_start_date?: string | null; tracking_start_value?: string | number | null }) =>
    send<Epoch>('PUT', '/settings/epoch', body),
  fetchEpochValue: (date?: string) =>
    send<Epoch & { fetched_value: number; applied: boolean }>(
      'POST',
      '/settings/epoch/fetch-value',
      { date, apply: true },
    ),

  // §7.1 — cash transactions
  cashTransactions: (source?: string) => get<CashList>(`/cash-transactions${query({ source })}`),
  createCashTransaction: (body: { date: string; type: string; amount: string; note?: string }) =>
    send<CashTransaction>('POST', '/cash-transactions', body),
  deleteCashTransaction: (id: number) => send<{ deleted: number }>('DELETE', `/cash-transactions/${id}`),
  pasteCashCsv: (text: string) =>
    send<{ inserted: number; rows_read: number; errors: string[]; note: string }>(
      'POST',
      '/cash-transactions/csv',
      { text },
    ),

  // §9 — charts
  chartAxes: () => get<AxisCatalog>('/charts/axes'),
  chart: (dimension: string, metric: string, extra: Record<string, string> = {}) =>
    get<Chart>(`/charts${query({ dimension, metric, ...extra })}`),
  chartFilterOptions: () => get<FilterOptions>('/charts/filter-options'),
  trend: (metric: string, window: number, extra: Record<string, string> = {}) =>
    get<Trend>(`/charts/trend${query({ metric, window: String(window), ...extra })}`),

  distribution: (metric = 'net_pnl', bins = 15) =>
    get<Distribution>(`/charts/distribution${query({ metric, bins: String(bins) })}`),

  regimeMarkers: () => get<{ items: RegimeMarker[]; note: string }>('/regime-markers'),
  createRegimeMarker: (body: { occurred_on: string; label: string; note?: string }) =>
    send<RegimeMarker>('POST', '/regime-markers', body),
  deleteRegimeMarker: (id: number) => send<{ deleted: number }>('DELETE', `/regime-markers/${id}`),

  dashboard: () => get<DashboardLayout>('/dashboard'),
  setDashboard: (panels: DashboardPanel[]) =>
    send<DashboardLayout>('PUT', '/dashboard', { panels }),
  resetDashboard: () => send<DashboardLayout>('DELETE', '/dashboard'),

  calendar: (year?: number, month?: number) =>
    get<CalendarBody>(
      `/calendar${query({ year: year?.toString(), month: month?.toString() })}`,
    ),

  // §5.4 — assignments, exercises, expirations
  optionEvents: (eventType?: string) =>
    get<{ total: number; items: OptionEvent[]; disclaimer: string }>(
      `/option-events${query({ event_type: eventType })}`,
    ),
  assignmentAlerts: () => get<AssignmentAlert>('/option-events/alerts'),
  acknowledgeOptionEvent: (id: number) =>
    send<{ id: number; acknowledged: boolean }>('POST', `/option-events/${id}/acknowledge`),

  // §11.1-11.2 — tags and notes
  tags: () => get<{ total: number; groups: string[]; items: Tag[] }>('/tags'),
  createTag: (body: { name: string; group_name?: string; color?: string }) =>
    send<Tag>('POST', '/tags', body),
  deleteTag: (id: number) => send<{ deleted: number }>('DELETE', `/tags/${id}`),
  tagTrade: (tradeId: number, body: { name: string; group_name?: string }) =>
    send<{ trade_id: number; tag: Tag }>('POST', `/trades/${tradeId}/tags`, body),
  untagTrade: (tradeId: number, tagId: number) =>
    send<{ removed: boolean }>('DELETE', `/trades/${tradeId}/tags/${tagId}`),
  dayTags: (day: string) => get<{ items: Tag[] }>(`/days/${day}/tags`),
  tagDay: (day: string, body: { name: string; group_name?: string }) =>
    send<{ trading_day: string; tag: Tag }>('POST', `/days/${day}/tags`, body),

  notes: (params: { scope?: string; scope_ref?: string; q?: string } = {}) =>
    get<{ total: number; items: Note[] }>(`/notes${query(params)}`),
  createNote: (body: { scope: string; scope_ref?: string; body_md: string }) =>
    send<Note>('POST', '/notes', body),
  updateNote: (id: number, body_md: string) => send<Note>('PUT', `/notes/${id}`, { body_md }),
  deleteNote: (id: number) => send<{ deleted: number }>('DELETE', `/notes/${id}`),

  // §10.3 — bulk operations
  bulkTrades: (body: {
    trade_ids: number[]
    action: string
    name?: string
    tag_id?: number
    stop_loss?: string | number
    profit_target?: string | number
    body_md?: string
  }) =>
    send<{
      action: string
      selected: number
      applied: number
      skipped: { trade_id: number; reason: string }[]
      note: string | null
    }>('POST', '/trades/bulk', body),

  // §11.3 — manual adjustments
  setTradeLevels: (tradeId: number, body: { stop_loss?: string | number; profit_target?: string | number }) =>
    send<{ trade_id: number; stop_loss: number | null; profit_target: number | null; note: string }>(
      'PUT',
      `/trades/${tradeId}/levels`,
      body,
    ),
  createManualExecution: (body: Record<string, unknown>) =>
    send<{ id: number; broker_trade_id: string; source: string; note: string }>(
      'POST',
      '/executions/manual',
      body,
    ),
  deleteManualExecution: (id: number) =>
    send<{ deleted: number }>('DELETE', `/executions/manual/${id}`),

  corporateActions: () =>
    get<{ total: number; items: CorporateActionRow[]; note: string }>('/corporate-actions'),
  createCorporateAction: (body: {
    instrument_id: number
    split_ratio: string | number
    occurred_on: string
    description?: string
  }) => send<{ id: number; split_ratio: number; applied: boolean; note: string }>(
    'POST',
    '/corporate-actions',
    body,
  ),
  deleteCorporateAction: (id: number) =>
    send<{ deleted: number }>('DELETE', `/corporate-actions/${id}`),
}
