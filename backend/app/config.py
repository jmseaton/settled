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
