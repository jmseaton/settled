"""§1.3a — the three endpoints the login flow needs.

All three are public (see PUBLIC_PATHS): the UI has to be able to ask
"is a password required, and am I past it?" before it has a session, and
logging out has to work with a cookie the server no longer accepts.
"""

import logging
from functools import lru_cache

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.service import SESSION_COOKIE, AuthService, LoginThrottled
from app.config import get_settings

log = logging.getLogger("settled.auth")

router = APIRouter()


@lru_cache
def get_auth_service() -> AuthService:
    """One instance per process: the throttle's counters are state, and a
    per-request service would forget every failed attempt."""
    return AuthService(get_settings())


def reset_auth_service() -> None:
    """Drop the cached service. For tests, and for anything that changes the
    settings after import — nothing in a running deployment does."""
    get_auth_service.cache_clear()


class LoginRequest(BaseModel):
    # No username field. There is one account; asking for its name would be
    # theatre, and a second thing to forget.
    password: str = Field(min_length=1, max_length=1024)


def set_session_cookie(request: Request, response: Response, token: str, ttl: int) -> None:
    auth = get_auth_service()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,  # the token is never read by JS, so keep it out of reach of XSS
        samesite="lax",  # blocks the cookie on cross-site POSTs; top-level GETs still work
        secure=auth.cookie_is_secure(request.url.scheme, request.headers.get("x-forwarded-proto")),
        path="/",
    )


@router.get("/session")
def read_session(request: Request):
    """Whether a password is required, and whether this browser is past it.

    Answers 200 either way. A 401 here would be indistinguishable from the
    401 that means "your session expired mid-page", and the UI needs to tell
    those apart to decide between the login screen and an error.
    """
    auth = get_auth_service()
    claims = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return auth.status(claims).as_dict()


@router.post("/login")
def login(request: Request, body: LoginRequest):
    auth = get_auth_service()
    if not auth.enabled:
        # Nothing to log in to. Saying so beats issuing a cookie that means
        # nothing and letting the UI believe it is protected.
        return JSONResponse(
            {"detail": "Authentication is not configured on this deployment."}, status_code=400
        )
    try:
        token = auth.login(body.password)
    except LoginThrottled as exc:
        return JSONResponse(
            {"detail": f"Too many failed attempts. Try again in {exc.retry_after}s."},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    except PermissionError:
        # The client IP is worth a line; the password is not, ever.
        log.warning("failed login attempt from %s", request.client.host if request.client else "?")
        return JSONResponse({"detail": "Incorrect password."}, status_code=401)

    # Read back the token we just issued rather than hand-assembling the
    # reply: one cheap HMAC, and the expiry the UI is told is the expiry the
    # cookie actually carries.
    response = JSONResponse(auth.status(auth.read_session(token)).as_dict())
    set_session_cookie(request, response, token, auth.ttl_seconds)
    return response


@router.post("/logout")
def logout(request: Request):
    auth = get_auth_service()
    response = JSONResponse({"enabled": auth.enabled, "authenticated": False, "session_expires_at": None})
    # delete_cookie has to match the attributes the cookie was set with or
    # the browser keeps the original alongside the expired one.
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=auth.cookie_is_secure(request.url.scheme, request.headers.get("x-forwarded-proto")),
    )
    return response
