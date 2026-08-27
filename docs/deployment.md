# Deploying Settled

Single host, Docker Compose, three containers: Postgres, the API with its
in-process daily scheduler, and nginx serving the built frontend.

Two things are optional and independent (§1.3a): a **password**, and
**TLS**. Neither is required to start. An install with neither behaves
exactly as this stack did before they existed — anyone who can reach the
port reads the account's full history and can trigger a sync — but it now
says so on startup and in a banner on every page, rather than silently.

Turn both on unless the box is genuinely yours alone and nothing else can
reach it. They answer different questions and neither substitutes for the
other:

| | Answers | Without it |
|---|---|---|
| Password | *Who is asking?* | Anyone on the network reads everything |
| TLS | *Who else is listening?* | The password and session cookie cross the network in clear text |

A password over plain HTTP is worth less than it looks: it is sent in the
clear on every sign-in. Set both, or set neither and keep the whole thing on
localhost behind an SSH tunnel.

---

## What the host needs

- Docker Engine with the Compose plugin (`docker compose version`)
- ~2GB disk to start: images are the bulk of it, the database grows slowly
- **To be powered on at 05:00 ET most mornings.** This is the real
  requirement, and it is worth being deliberate about — see "Why uptime
  matters more than it looks" below.

## First run

```bash
git clone <your remote> settled && cd settled
cp .env.example .env
$EDITOR .env          # POSTGRES_PASSWORD is required; Flex credentials optional
./scripts/generate-self-signed-cert.sh    # optional, but see below
docker compose up -d --build
docker compose logs -f backend
```

The backend entrypoint waits for Postgres, creates any missing tables, and
reports schema drift before uvicorn starts. Healthy startup looks like:

```
[entrypoint] database reachable after 1s
[entrypoint] creating any missing tables...
Schema created.
Schema matches the ORM metadata.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Then open `http://<host>:8080` — or `https://<host>:8443` if you generated
a certificate, in which case 8080 redirects there.

With no password configured the log leads with the reason to set one:

```
WARNING  settled: NO AUTHENTICATION CONFIGURED — anyone who can reach this
port can read the account's full position and P&L history and trigger a sync.
```

and nginx says the same about the transport:

```
[settled-tls] No certificate at /etc/nginx/certs/fullchain.pem — serving plain HTTP.
[settled-tls] The session cookie and your password cross the network in the clear.
```

Both are fixed in the two sections below and neither blocks startup.

With Flex credentials set you will also see the scheduler announce itself:

```
Scheduler started; next run at 2031-08-11 05:00:00-04:00
```

Without them: `Scheduler not started: Flex credentials are not configured`.
That is a valid deployment — the app runs upload-only — but it collects no
marks, so read the uptime section before settling for it.

### Setting the epoch

Before the numbers mean anything, set the tracking start date and value in
Settings (§0.3). Every return, TWR and benchmark comparison is anchored to
that epoch, and changing it later triggers a full rebuild.

## Turning on the password

```bash
docker compose exec backend python -m app.auth
```

It prompts twice and prints one line — the password itself never reaches
argv, your shell history, or `ps`. Paste that line into `.env`:

```
SETTLED_AUTH_PASSWORD_HASH=scrypt:16384:8:1:...
```

Then `docker compose up -d` (not `restart` — `.env` is read at container
creation). The warning is replaced by:

```
INFO     settled: Authentication is on; sessions last 336h.
```

The UI asks for the password on next load; nothing else about the app
changes.

What it is and is not:

- **One owner, one password.** No user table, no accounts, no roles, no
  password-reset flow. Losing the password means generating a new hash and
  restarting — a runbook step, not a feature.
- The password is stored as an **scrypt hash**, never in plaintext, and
  never in the database (§3.10's rule, applied to one more credential).
- Changing the hash **invalidates every existing session**, because the
  cookie signing key is derived from it. That is what a password change
  should do, and it needs nothing revoked.
- Sessions last `SETTLED_SESSION_TTL_HOURS` (default 14 days) and slide: a
  browser in daily use is re-issued a cookie past the halfway mark, so it
  never gets logged out mid-week on a fixed schedule.
- Failed attempts back off exponentially, capped at 30 seconds. It is a
  slowdown, not a lockout — fat-fingering it costs you half a minute.

### Refusing to run without one

```
SETTLED_AUTH_REQUIRED=true
```

The backend then exits at startup rather than serving unauthenticated. Off
by default so an existing install upgrades without an outage; on if you
would rather have a crash-looping container than an open one.

### The API token, for the host cron

Turning the password on breaks any script hitting the API, including the
host-cron sync path documented below — `curl` has no cookie jar. Give those
a bearer token instead:

```bash
docker compose exec backend python -m app.auth --api-token
```

```
SETTLED_API_TOKEN=<the printed value>
```

```bash
curl -fsS -X POST -H "Authorization: Bearer $SETTLED_API_TOKEN" \
    https://localhost:8443/api/sync/run
```

Leave it empty and there is no bearer path at all. It grants the same access
the password does, so it belongs in `.env` and in the cron line's
environment, nowhere else.

`/health` stays open either way: it is what the container healthcheck calls,
and it reports liveness and nothing else.

---

## Turning on TLS

nginx serves HTTPS when it finds `certs/fullchain.pem` and
`certs/privkey.pem` next to `docker-compose.yml`, and plain HTTP when it
does not. There is no third setting to keep in sync.

### On a LAN, with a self-signed certificate

```bash
./scripts/generate-self-signed-cert.sh                    # guesses this host
./scripts/generate-self-signed-cert.sh settled.lan 192.168.1.40
docker compose up -d
```

Then `https://<host>:8443`. Port 8080 keeps answering and redirects there.

The first visit warns, because nobody has vouched for the certificate. That
warning is doing real work — click through it on a network you do not
control and you have accepted whatever answered — so silence it properly
rather than getting used to it: import `certs/fullchain.pem` into the trust
store of each machine you use, as a trusted *certificate*, not as a
certificate authority.

The traffic is encrypted identically either way. What a self-signed
certificate lacks is a third party asserting the host is who it claims, and
on your own LAN you are better placed to check that than any CA is.

### With a real certificate

If the host has a name pointed at it, get a certificate from Let's Encrypt
however you like — a `certbot` container, DNS-01 from another machine, your
router — and put the results at `certs/fullchain.pem` and
`certs/privkey.pem`. nginx reads them at container start, so renewals need a
`docker compose restart web` (a monthly cron line is enough).

Only then consider `SETTLED_HSTS=on`. With a self-signed certificate HSTS is
actively harmful: it converts the click-through warning into a wall with no
bypass, on the one host you need to reach.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `SETTLED_TLS` | `auto` | `auto` serves TLS if a certificate is present; `on` refuses to start without one; `off` is plain HTTP with no warning |
| `SETTLED_TLS_PORT` | `8443` | Published HTTPS port — also what the HTTP redirect points at, so the two cannot disagree |
| `SETTLED_HSTS` | `off` | Only with a real certificate |

`SETTLED_BIND=127.0.0.1` still works and still composes with both: password,
TLS and an SSH tunnel are three independent layers, and on a machine you
share with anyone the tunnel is the strongest of them.

---

## Day-to-day

```bash
docker compose ps                    # what is running
docker compose logs -f backend       # sync activity, scheduler decisions
docker compose restart backend       # after an .env change
docker compose down                  # stop; volumes and data survive
```

`.env` is read at container start. Editing it does nothing until you
`docker compose up -d` (which recreates containers whose config changed) or
restart the service.

### Forcing a sync

```bash
curl -fsS -X POST http://localhost:8080/api/sync/run
# with a password configured, and TLS:
curl -fsS -X POST -H "Authorization: Bearer $SETTLED_API_TOKEN" \
    https://localhost:8443/api/sync/run
```

Synchronous — it runs the two-step Flex fetch and returns the import report,
so expect it to take a while rather than answering immediately. The run is
recorded in sync history with trigger `MANUAL`, exactly as a scheduled one
is, and it takes the same advisory lock, so firing this while the 05:00 job
is running gets a `skipped_locked` rather than two concurrent syncs.

It returns HTTP 400 if Flex credentials are not configured, and it takes no
date parameters: it fetches the query's own window, ending today. **There is
no API path that forces a statement for a past date**, so this cannot fill a
missed day's marks (see below).

Check what it did:

```bash
curl -fsS http://localhost:8080/api/sync/runs | head -c 2000
curl -fsS http://localhost:8080/api/sync/health
```

(Add the `Authorization: Bearer` header to both once a password is set —
every `/api/` path needs it, `/health` does not.)

## Upgrading

```bash
git pull
docker compose up -d --build
```

### Upgrading when the schema changed

**Read this before pulling a version that adds a column.**

Alembic is the migration tool of record (§13), but there is no migration
history yet — the schema is created from ORM metadata. `create_all` creates
tables that do not exist and **never alters tables that do**. So an upgrade
that adds a column finds every table already present, changes nothing, and
the app runs against a schema that is missing it. The symptom arrives later
as an `UndefinedColumn` error from whichever page reads that column first.

The entrypoint checks for this on every start and says so:

```
SCHEMA DRIFT — the database does not match this version of the app.
  trades is missing: mfe_price, mae_price
  create_all() adds tables but never alters them, so this will not
  resolve itself on restart.
```

It reports rather than repairs, because guessing an `ALTER` for a column
whose type and default it cannot infer is how a personal tool loses data.
The app still boots — refusing to start would turn a bad upgrade into an
outage with no UI to read the explanation from.

To resolve it, recreate the schema and re-import. This is safe **only**
because every derived table is rebuildable from the archived payloads in
§3.2 — which is exactly what the `settled-data` volume exists to protect:

```bash
# Confirm the payload archive is intact FIRST — this is what you rebuild from.
docker compose exec backend ls -la /data

docker compose down
docker volume rm settled_settled-db      # destroys derived data only
docker compose up -d --build
# then re-import the archived payloads through the UI
```

Manual entries do not live in the payload archive — manual cash
transactions, tags, notes, journal entries, stops and targets, regime
markers, and manual basis entries (§5.5) are yours, not the broker's, and a
`docker volume rm` takes them with it. Back the database up first:

```bash
docker compose exec db pg_dump -U settled settled > settled-$(date +%F).sql
```

## Backups

Two volumes, and they are not equally replaceable.

| Volume | Holds | If lost |
|---|---|---|
| `settled_settled-data` | §3.2 archived Flex payloads, keyed by SHA-256 | **Unrecoverable.** Beyond IBKR's ~1-year retention the source statements are gone for good. |
| `settled_settled-db` | Everything derived, plus all manual entries | Derived data rebuilds from the archive; manual entries do not. |

A weekly host cron is enough:

```cron
0 3 * * 0 cd /srv/settled && docker compose exec -T db pg_dump -U settled settled | gzip > /backup/settled-$(date +\%F).sql.gz
0 4 * * 0 docker run --rm -v settled_settled-data:/data -v /backup:/backup alpine tar czf /backup/settled-data-$(date +\%F).tar.gz -C /data .
```

Both files contain real position and P&L data. Treat them the way you treat
a brokerage statement.

## Why uptime matters more than it looks

The daily sync is not only how trades arrive — trades are backfilled by the
trailing 30-day window, so a missed day costs nothing there.

It is how **marks** arrive, and marks do not backfill. Each Flex statement
reports open positions as of its `period_end`, so one sync run contributes
exactly one day of marks no matter how wide its window. A morning the
scheduler does not fire is a permanently missing day in every open
position's mark path.

That path is the account's unrealized-P&L series, and it is the only
available source of MFE/MAE for options (Phase 5 — see the spec's §12).
Options intraday history is expensive and futures options are not served by
any free provider, so accumulated EOD marks are not a cheap approximation of
excursion data, they are the whole of it.

Practical consequences:

- Uptime at 05:00 ET is the requirement. A box that sleeps is a box with a
  gappy mark history.
- APScheduler is configured with a six-hour misfire grace, so a container
  restarting at 04:55 still runs. A host down all morning does not.
- Check the staleness badge in the UI occasionally. `STALENESS_BUSINESS_DAYS`
  is what turns "the sync has quietly not run since March" into something
  visible.
- An expired Flex token stops sync exactly the way downtime does, and it
  will happen — tokens commonly last a year. Rotating it is an `.env` edit
  plus `docker compose up -d`.

## Running sync from a host cron instead

Set `SETTLED_SCHEDULER_ENABLED=false` and drive it externally:

```cron
0 5 * * 1-5 curl -fsS -X POST -H "Authorization: Bearer $SETTLED_API_TOKEN" http://localhost:8080/api/sync/run
```

(Drop the header if no password is configured. Keep the token out of the
crontab itself — put it in the cron user's environment or a sourced file
that is mode 600, since a crontab is world-readable on some systems.)

Note this fires on weekdays only and does not know the market calendar, so
it will attempt a sync on holidays; the app skips non-trading days itself.
The in-process scheduler is the better default — it knows the calendar, it
handles misfires, and it takes a Postgres advisory lock so two of them
cannot sync at once.

## Troubleshooting

**`POSTGRES_PASSWORD` error on `up`** — compose refuses to start without it.
Set it in `.env`.

**Locked out — the password is gone** — there is nothing to recover, and
nothing is lost. Generate a new hash, put it in `.env`, `docker compose up -d`.
Data is untouched; every existing session is invalidated, which is the point.

**"Incorrect password" for a password you are certain of** — check what
actually reached the container:

```bash
docker compose exec backend printenv SETTLED_AUTH_PASSWORD_HASH
```

If it is empty, truncated, or missing its numbers, `.env` mangled it.
Compose interpolates `$` in values, so a hash containing `$` arrives with
`$16384` and friends replaced by nothing — which is why generated hashes are
colon-separated. A hash generated before that change still verifies, but
only if it survives `.env` intact; regenerate it if this is what you see.
Do not wrap the value in quotes.

If the value is right and login still fails, the backend says so at startup:
a hash it cannot parse is a configuration error, not a wrong password, and
it is logged as one.

**Every API call 401s after turning the password on** — expected for
anything that is not the browser. Scripts, the host cron, and any bookmarked
`curl` need `Authorization: Bearer $SETTLED_API_TOKEN`. `/health` does not.

**The browser warns about the certificate** — expected with a self-signed
one on first visit. Import `certs/fullchain.pem` into that machine's trust
store to stop it, rather than clicking through each time. A warning that
appears on a host that previously did *not* warn is worth stopping to read:
either the certificate was regenerated, or something else is answering.

**The certificate script fails, or `certs/` is owned by root** — `docker
compose up` creates a missing bind-mount source itself, as root, so
starting the stack before generating a certificate leaves a directory the
script cannot write into. The repo ships a `certs/.gitkeep` so the
directory normally arrives with the checkout, but an install that predates
it needs:

```bash
sudo chown -R "$(id -u):$(id -g)" certs
```

**`https://` refuses the connection, `http://` works** — nginx found no
certificate and fell back:

```bash
docker compose logs web | grep settled-tls
```

`No certificate at ...` means `certs/fullchain.pem` and `certs/privkey.pem`
are not both present and readable inside the container. Check the names
exactly, and that `./certs` is where `docker-compose.yml` is. Set
`SETTLED_TLS=on` to make this a startup failure instead of a silent
fallback.

**The HTTP redirect lands on a port nothing answers** — `SETTLED_TLS_PORT`
and the published HTTPS port have to be the same value; the redirect is
built from the former. Both come from `.env`, so they only disagree if one
was overridden elsewhere.

**Browser refuses to load over HTTP after turning HSTS off** — HSTS is
remembered by the browser, not re-checked. Clear it for that host in the
browser's site settings. This is why it is off by default and why it should
stay off with a self-signed certificate.

**Backend restarting** — `docker compose logs backend`. Most often the
database URL and the Postgres credentials disagree after a `.env` edit that
changed `POSTGRES_USER` without recreating the db volume; the volume keeps
the credentials it was initialized with.

**UI loads, every request 502** — the backend is not healthy yet or has
crashed. `docker compose ps` shows its state; nginx proxies to `backend:8000`
by service name, so this is nearly always the backend rather than nginx.

**Sync fails with a credential error** — Flex errors are classified (§3.8);
credential failures are deliberately not retried. Check the token has not
expired and that the query ID matches an *Activity* Flex Query.

**A Flex request fails and the message does not say why** — run the probe:

```bash
docker compose exec backend python -m app.flex.diagnose
```

**Every** request the app makes overrides the query's dates — the sync
runner sends a trailing 30-day `fd`/`td` window (§3.8), the epoch fetch
sends a single day (§0.3). So "sync fails too" does *not* rule the override
out; both carry it. The probe makes one request with no date parameters at
all, letting IBKR use the query's own configured period, one with the
`fd`/`td` override, and one with `p`, and prints all three responses. If the
first works and the second does not, the Flex query does not permit `fd`/`td`
overrides. If all three fail identically, the override is not the cause and
the printed bodies are the evidence.

It is read-only, writes nothing, and redacts the token.

**Sync succeeds but warns that the date override was rejected** — the query's
stored period is a relative one, and those refuse `fd`/`td` (§3.9). The sync
falls back to the query's own period and records the window it actually
covered, so this is a working sync rather than a broken one — but the window
is the query's, not the 30 days §3.8 asks for. Set **Number of Days** to 90
or more on the query and the warning stops mattering.

There is nothing else to change in Client Portal, and one trap is worth
knowing. There are two Period dropdowns. The ad-hoc **Run** dialog offers a
Custom Date Range; the query's **Delivery Configuration** — the section
marked "Applicable for Email, FTP and Flex Web Service", which is the one the
API actually reads — does not. Seeing the option while running the query by
hand is not evidence the web service can use it. The single-day epoch NAV
fetch (§0.3) has no fallback available for the same reason, so on such a
query that value has to be typed.

**"Temporary failure in name resolution" partway through a sync** — the two
Flex calls do not use the same hostname. SendRequest goes to
`ndcdyn.interactivebrokers.com`, which is in the source; GetStatement goes
to whatever `<Url>` came back, in practice `gdcdyn.interactivebrokers.com`,
which is not. A resolver that answers for one and not the other therefore
fails *after* SendRequest has succeeded, against a host that appears nowhere
in the code. Both names are CNAMEs onto the same Akamai edge
(`www.interactivebrokers.com.edgekey.net`), so a resolver treating them
differently is broken rather than making a distinction that means anything.

Seen in practice from a consumer router at `192.168.1.1` that resolved
`ndcdyn` and returned SERVFAIL — not NXDOMAIN — for `gdcdyn`. Confirm by
asking a public resolver directly, from the host:

```bash
nslookup gdcdyn.interactivebrokers.com 1.1.1.1   # should answer 23.x.x.x
resolvectl status | grep -E 'Current DNS|DNS Servers'
```

If the public resolver answers and the configured one does not, replace it.
On a netplan host the nameservers usually arrive over DHCP, so the DHCP
ones have to be refused as well as overridden — setting `DNS=` in
`/etc/systemd/resolved.conf` alone will not take, because per-link servers
win:

```yaml
      dhcp4: true
      dhcp4-overrides:
        use-dns: false
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```

Then `sudo netplan apply && sudo resolvectl flush-caches`, and restart the
backend so the container picks the new servers up — it resolves through
Docker's embedded DNS, which forwards to the host's and caches the old
answer otherwise. Verify from *inside* the container, which is where it has
to work:

```bash
docker compose restart backend
docker compose exec backend python -c \
  "import socket; print(socket.getaddrinfo('gdcdyn.interactivebrokers.com',443)[0][4])"
```

DNSSEC is worth ruling out early rather than assuming: if both hostnames
share a CNAME chain and only one fails, validation is not what is happening,
and setting `DNSSEC=false` will change nothing.

**"the query's CSV/text output rather than XML"** — the Flex query's
**Format** is set to text. It is a separate setting from the period, and
nothing about the request reveals it: both calls answer 200 and look
perfectly healthy, and only the body gives it away. Fix it in Client Portal
→ Performance & Reports → Flex Queries → edit the query → Format → XML. No
redeploy needed; the next sync picks it up.

**"Unparseable response" or "Expected a Flex statement, got \<html\>"** —
IBKR answered with something that is not a statement, usually a maintenance
or gateway page. The error quotes the first 200 characters of what arrived
(token redacted) so the page identifies itself. Both are transient in the
ordinary case; a `<html>` body that persists for hours is IBKR-side.

**Benchmark line is stale** — a price fetch failure is non-fatal by design
(§7.5). The last cached bar stays, marked stale. It will retry next run.
