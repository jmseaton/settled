import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.auth.routes import get_auth_service, router as auth_router, set_session_cookie
from app.auth.service import PUBLIC_PATHS, SESSION_COOKIE
from app.config import get_settings
from app.flex.scheduler import shutdown_scheduler, start_scheduler

# Uvicorn configures handlers for its own loggers and none for the root, so
# without this the app's own log lines are invisible below WARNING — where
# they arrive via logging.lastResort, unformatted and without a level. That
# is not good enough for messages whose whole job is to tell you what
# security posture the process came up in (§1.3a), or for the scheduler
# saying whether it started (§3.8). basicConfig is a no-op if handlers are
# already installed, so an operator supplying their own config still wins.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("settled")

# Methods that change something. GET and HEAD are exempt from the origin
# check because a cross-site GET cannot be made to do anything a bookmark
# could not, and the read endpoints have no side effects.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEV_ORIGIN = "http://localhost:5173"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    auth = get_auth_service()

    if auth.enabled:
        log.info("Authentication is on; sessions last %sh.", settings.session_ttl_hours)
    elif settings.auth_required:
        # The one configuration that is refused outright. SETTLED_AUTH_REQUIRED
        # exists precisely so that "serving the account's full history to the
        # network with no password" cannot happen by omission.
        raise RuntimeError(
            "SETTLED_AUTH_REQUIRED is set but SETTLED_AUTH_PASSWORD_HASH is empty. "
            "Generate one with `python -m app.auth` or unset SETTLED_AUTH_REQUIRED."
        )
    else:
        log.warning(
            "NO AUTHENTICATION CONFIGURED — anyone who can reach this port can read the "
            "account's full position and P&L history and trigger a sync. Set "
            "SETTLED_AUTH_PASSWORD_HASH (see `python -m app.auth`) or bind to 127.0.0.1."
        )

    # Returns None when disabled or when Flex credentials are absent, so an
    # upload-only deployment simply has no scheduler rather than a job that
    # fails every morning (§3.8).
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    lifespan=lifespan,
    title="Settled — Trade Performance Tracker",
    version="0.1.0",
    description=(
        "Performance attribution for a personal IBKR account. "
        "Not a tax record: FIFO matching serves performance attribution only, "
        "with no wash-sale or tax-lot handling (§1.3)."
    ),
)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value if scheme.lower() == "bearer" and value else None


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    """§1.3a — the gate.

    Middleware rather than a per-route dependency on purpose: a route added
    later is closed without anyone remembering to close it. The cost is that
    the exemptions live in one list instead of beside the routes, which for
    four paths is the better trade.
    """
    auth = get_auth_service()
    path = request.url.path

    # No password configured: behave exactly as the app always has. The
    # warning at startup and the banner in the UI are what make this visible
    # rather than silent.
    if not auth.enabled:
        return await call_next(request)

    # CORS preflight carries no credentials by definition and must answer
    # before the browser will send the real request.
    if request.method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)

    claims = auth.read_session(request.cookies.get(SESSION_COOKIE))
    if claims is None:
        # The bearer path is for the documented host-cron sync (§3.8) and
        # anything else without a cookie jar. Absent SETTLED_API_TOKEN it is
        # not a path at all.
        if not auth.check_api_token(_bearer_token(request)):
            return JSONResponse({"detail": "Authentication required."}, status_code=401)
        return await call_next(request)

    # Cookie-authenticated and state-changing: check the request came from
    # our own page. Bearer requests skip this — a token is never attached
    # automatically by a browser, so there is no cross-site case to guard.
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin != DEV_ORIGIN and not auth.origin_is_acceptable(
            origin, request.headers.get("host")
        ):
            return JSONResponse(
                {"detail": "Cross-origin request rejected."}, status_code=403
            )

    response = await call_next(request)

    # Sliding expiry. Re-issue once the session is past its half-life, so a
    # browser in daily use never hits the wall, and one left alone for the
    # full TTL still expires.
    if auth.should_refresh(claims, time.time()):
        set_session_cookie(request, response, auth.issue_session(), auth.ttl_seconds)
    return response


# Registered last, and that is what puts it outermost: Starlette builds the
# stack so the most recently added middleware wraps the rest. CORS therefore
# sees — and can label — the 401 that require_authentication returns. The
# other order leaves a rejected cross-origin request looking like a CORS
# failure in the browser console instead of the sign-in prompt it is.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[DEV_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    """Unauthenticated by design: it is what the container healthcheck and
    the compose dependency gate call, and it reports liveness only."""
    return {"status": "ok"}
