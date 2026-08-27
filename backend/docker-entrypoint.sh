#!/bin/sh
# Bring the schema up before serving, then hand off to uvicorn as PID 1 so
# `docker compose stop` reaches the app and the scheduler shuts down cleanly
# instead of being killed after the grace period.
set -e

echo "[entrypoint] waiting for the database..."
python - <<'PY'
import sys
import time

from sqlalchemy import text

from app.db import engine

# Compose already gates on pg_isready, but "accepting connections" and
# "ready for our role and database" are not the same instant, and a restart
# after a host reboot races both.
DEADLINE = 60
for attempt in range(DEADLINE):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        print(f"[entrypoint] database reachable after {attempt}s")
        break
    except Exception as exc:  # noqa: BLE001 — any failure here means "not yet"
        if attempt == DEADLINE - 1:
            print(f"[entrypoint] database unreachable after {DEADLINE}s: {exc}")
            sys.exit(1)
        time.sleep(1)
PY

echo "[entrypoint] creating any missing tables..."
python -m app.schema_setup

# Reports, never repairs — see app/schema_check.py for why. A drifted schema
# still boots: refusing to start would turn a bad upgrade into an outage with
# no UI to read the explanation from.
python -m app.schema_check

exec "$@"
