from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SETTLED_", env_file=".env")

    database_url: str = "postgresql+psycopg://settled:settled_dev@localhost:5432/settled"

    # §14.1: futures/futures-option session boundary, ET.
    session_start_hour: int = 18

    # §9.5: default lookahead window for the Expiry Watch panel, calendar days.
    expiry_watch_window_days: int = 5

    # §3.10 — Flex credentials come from the environment and are never
    # persisted. The token grants read access to the full statement history,
    # so it stays out of the database, out of source control, and out of any
    # log line. Environment variables are acceptable for a single-host
    # personal deployment; a plaintext file in the repo is not.
    flex_token: str = ""
    flex_query_id: str = ""

    # §3.8 — the in-process daily scheduler. Off in tests and in any
    # deployment that drives sync externally (a host cron, say), so a
    # process never schedules work it was not meant to own.
    scheduler_enabled: bool = True

    # §3.2 — "Store the file. Keep the original upload on disk keyed by
    # SHA-256, so a full rebuild is always possible." Without this the raw
    # payload is unrecoverable once a Flex window rolls off.
    data_dir: str = "./data"

    # §7.5 — benchmark price data. `auto` prefers Tiingo when a key is set
    # and falls back to keyless Stooq when it is not, so a fresh install
    # still draws a benchmark line rather than a configuration error. The
    # key follows the same rule as the Flex token (§3.10): environment
    # only, never the database.
    price_source: str = "auto"
    tiingo_api_key: str = ""

    # §7.5 — "Failures are non-fatal: log, retry next run, show the
    # benchmark line as stale rather than blanking the chart." Set false to
    # skip the price fetch entirely (tests, or an offline deployment).
    price_fetch_enabled: bool = True

    # --- §1.3a authentication -------------------------------------------
    # The spec's §1.3 put auth out of scope on the grounds of "single user,
    # local/LAN". That holds right up until the host is reachable from
    # anywhere else, and this is an account's complete position and P&L
    # history — so the door is now optional rather than absent.
    #
    # Auth is on exactly when a password hash is configured. Empty means the
    # app runs open, as it always has, but says so in the log and in a
    # banner on every page rather than silently. Generate a hash with:
    #     docker compose exec backend python -m app.auth
    auth_password_hash: str = ""

    # Refuse to start with auth unconfigured. Off by default so an existing
    # deployment upgrades without a surprise outage; on for anyone who would
    # rather fail loudly than serve their statements to the LAN.
    auth_required: bool = False

    # Signing key for session cookies. Empty derives one from the password
    # hash, which is unique per install (the hash carries a random salt) and
    # invalidates every session when the password changes. Set it explicitly
    # only to keep sessions alive across a password rotation.
    auth_secret: str = ""

    # A personal tool that demands a password every morning gets its password
    # written on a sticky note. Fourteen days, sliding on use.
    session_ttl_hours: int = 24 * 14

    # Non-browser access for the documented host-cron path (a bare
    # `curl -X POST /api/sync/run` has no cookie to send). Empty disables
    # bearer auth entirely; when set it must be long and random.
    api_token: str = ""

    # Secure cookies. `auto` sets the flag when the request arrived over
    # TLS — directly or via X-Forwarded-Proto from nginx — which is right
    # for both the plain-HTTP and the TLS deployment without a second knob
    # to keep in sync. Force it with true/false if a proxy lies.
    cookie_secure: str = "auto"

    # §1.3a — reject a state-changing request whose Origin is another site.
    # SameSite=Lax already blocks the cookie on cross-site POSTs; this is
    # the belt to that pair of braces. Disable only if a proxy rewrites
    # Origin and Host so they cannot agree.
    auth_strict_origin: bool = True

    # §8.2 / §14.6.3 — how many observations before the risk-adjusted
    # ratios are drawn at all. Configurable because it is a display
    # preference, not a statistical claim: every Sharpe now carries its own
    # confidence interval, and that is what says whether to believe it.
    # Raising this hides noisy early numbers; lowering it shows them with
    # their (wide) intervals attached.
    min_sample_trading_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
