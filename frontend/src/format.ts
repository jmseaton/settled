export const money = (v: number | null | undefined, dp = 2): string =>
  v === null || v === undefined
    ? '—'
    : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: dp, maximumFractionDigits: dp })

export const num = (v: number | null | undefined, dp = 2): string =>
  v === null || v === undefined ? '—' : v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })

export const pct = (v: number | null | undefined, dp = 1): string =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(dp)}%`

export const signClass = (v: number | null | undefined): string =>
  v === null || v === undefined || v === 0 ? '' : v > 0 ? 'pos' : 'neg'

export const duration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

// §14.1 — every displayed time is Eastern, because that is the zone the
// trading day is defined in and the zone the schedule is stated in.
//
// These used to slice the ISO string and label the result ET, on the premise
// that the API already serialized in America/New_York. It does not. Every
// timestamp is a Postgres `timestamptz`, which comes back in the session
// zone — Etc/UTC — so the slice showed UTC under an ET label. The stock
// execution that §16 test 15 records at 09:49:09 ET displayed as 13:49:09.
//
// The old comment warned that parsing into a Date would re-render in the
// browser's zone. That is only true without an explicit `timeZone`; with one
// the output is Eastern no matter where the browser is, which is the point.
const EASTERN = 'America/New_York'

const etFormatter = (withSeconds: boolean) =>
  // en-CA for YYYY-MM-DD, h23 so midnight is 00 rather than 24.
  new Intl.DateTimeFormat('en-CA', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' as const } : {}),
    hourCycle: 'h23',
  })

const WITH_SECONDS = etFormatter(true)
const WITHOUT_SECONDS = etFormatter(false)

const inEastern = (iso: string | null, formatter: Intl.DateTimeFormat): string | null => {
  if (!iso) return null
  // A timestamp with no offset would be read as browser-local, silently
  // reintroducing the bug above. Everything here comes from a timestamptz
  // and carries one; anything that does not is treated as UTC, which is what
  // the database holds.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const parsed = new Date(normalized)
  if (Number.isNaN(parsed.getTime())) return null
  return formatter.format(parsed).replace(', ', ' ')
}

export const dateTime = (iso: string | null): string => inEastern(iso, WITH_SECONDS) ?? '—'

/** Same conversion, with the zone named — for times a user acts on, like a
 *  scheduled sync, where "05:00" alone invites the wrong assumption. */
export const dateTimeET = (iso: string | null): string => {
  const formatted = inEastern(iso, WITHOUT_SECONDS)
  return formatted ? `${formatted} ET` : '—'
}
