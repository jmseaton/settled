"""§1.3a — the one gate in front of the account's history.

Auth is *configured*, not merely *enabled*: it is on exactly when a password
hash is present. There is no `AUTH_ENABLED=true` that can be true while the
password is empty, because that combination has only two possible readings —
locked out, or wide open — and both are worse than refusing to accept it.
"""

import hmac
import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.auth.passwords import PasswordHashError, parse_hash, verify_password
from app.auth.sessions import InvalidSession, derive_secret, issue, verify
from app.config import Settings

log = logging.getLogger("settled.auth")

SESSION_COOKIE = "settled_session"

# Paths that answer before a session exists. Everything else under /api is
# closed. An explicit set rather than a prefix match, so a future
# /api/auth/anything is closed by default and has to be opted in here.
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/api/auth/session",
        "/api/auth/login",
        "/api/auth/logout",
    }
)

# Login throttle. scrypt already costs ~50-100ms per attempt, which alone
# puts an online guessing attack in the millennia; this caps the rate a
# stolen laptop on the LAN can try at, and bounds the CPU a flood can burn.
# Global rather than per-IP on purpose: one user, so there is no legitimate
# second client to starve, and per-IP buckets are trivially defeated by the
# one attacker who has more than one address. The cap keeps it a slowdown
# rather than a lockout — the owner waits 30s, not forever.
THROTTLE_FREE_ATTEMPTS = 3
THROTTLE_BASE_SECONDS = 1.0
THROTTLE_MAX_SECONDS = 30.0


class LoginThrottled(Exception):
    """Too soon after a failure. Carries how long is left, so the UI can say
    so rather than showing a bare failure the user retries immediately."""

    def __init__(self, retry_after: float):
        super().__init__(f"retry in {retry_after:.0f}s")
        self.retry_after = max(1, int(round(retry_after)))


class LoginThrottle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures = 0
        self._next_allowed = 0.0

    def check(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if now < self._next_allowed:
                raise LoginThrottled(self._next_allowed - now)

    def record_failure(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._failures += 1
            over = self._failures - THROTTLE_FREE_ATTEMPTS
            if over > 0:
                delay = min(THROTTLE_BASE_SECONDS * (2 ** (over - 1)), THROTTLE_MAX_SECONDS)
                self._next_allowed = now + delay

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._next_allowed = 0.0


@dataclass(frozen=True)
class AuthStatus:
    """What /api/auth/session tells the UI. It deliberately reports whether
    auth is configured at all: an open install should be visibly open."""

    enabled: bool
    authenticated: bool
    session_expires_at: int | None = None

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "authenticated": self.authenticated,
            "session_expires_at": self.session_expires_at,
        }


class AuthService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self.throttle = LoginThrottle()
        self._password_hash = settings.auth_password_hash.strip()
        self._api_token = settings.api_token.strip()
        if self._password_hash:
            # Parse here, at construction, rather than at the first login: a
            # typo in the environment should be a startup error, not a
            # "wrong password" for someone typing the right one.
            parse_hash(self._password_hash)
            self._secret = derive_secret(self._password_hash, settings.auth_secret.strip())
        else:
            self._secret = b""

    @property
    def enabled(self) -> bool:
        return bool(self._password_hash)

    @property
    def ttl_seconds(self) -> int:
        return max(1, int(self._settings.session_ttl_hours)) * 3600

    def login(self, password: str) -> str:
        """Return a fresh session token, or raise.

        The throttle is checked before the hash is computed, so a flood
        cannot make the box do scrypt work at line rate.
        """
        if not self.enabled:
            raise PermissionError("authentication is not configured")
        self.throttle.check()
        try:
            ok = verify_password(password, self._password_hash)
        except PasswordHashError:
            log.error("SETTLED_AUTH_PASSWORD_HASH is not a usable hash; nobody can log in")
            raise
        if not ok:
            self.throttle.record_failure()
            raise PermissionError("incorrect password")
        self.throttle.record_success()
        return issue(self._secret, ttl_seconds=self.ttl_seconds)

    def issue_session(self) -> str:
        """A fresh token for an already-authenticated caller. Used by the
        sliding refresh; login goes through login() so the throttle sees it."""
        if not self.enabled:
            raise PermissionError("authentication is not configured")
        return issue(self._secret, ttl_seconds=self.ttl_seconds)

    def should_refresh(self, claims: dict, now: float | None = None) -> bool:
        """True past the session's half-life. Re-issuing on every request
        would rewrite the cookie on every poll of the sync banner; re-issuing
        never would log the owner out mid-week on a fixed schedule."""
        now = now if now is not None else time.time()
        return (claims["exp"] - now) < (self.ttl_seconds / 2)

    def read_session(self, token: str | None) -> dict | None:
        if not self.enabled or not token:
            return None
        try:
            return verify(self._secret, token)
        except InvalidSession:
            return None

    def check_api_token(self, presented: str | None) -> bool:
        if not self._api_token or not presented:
            return False
        return hmac.compare_digest(self._api_token, presented.strip())

    def cookie_is_secure(self, request_scheme: str, forwarded_proto: str | None) -> bool:
        configured = self._settings.cookie_secure.strip().lower()
        if configured in {"true", "1", "yes", "on"}:
            return True
        if configured in {"false", "0", "no", "off"}:
            return False
        # `auto`: trust X-Forwarded-Proto when nginx set it. The app itself
        # always speaks plain HTTP behind the proxy, so without this a TLS
        # deployment would never mark a cookie secure.
        proto = (forwarded_proto or "").split(",")[0].strip().lower()
        return (proto or request_scheme) == "https"

    def origin_is_acceptable(self, origin: str | None, host: str | None) -> bool:
        """Same-origin check for state-changing requests.

        A missing Origin passes: curl and the documented host cron send
        none, and a browser that omits it is not the cross-site case this
        guards. What it rejects is another site's page posting here with the
        cookie riding along.

        Hostnames only, ports ignored — and that is not a shortcut. nginx
        forwards `Host: $host`, which strips the port, while the browser's
        Origin carries it: on the TLS deployment the pair is
        `https://settled.lan:8443` against `settled.lan`, and comparing them
        whole would reject every write. Ports are also not a boundary
        cookies respect — a cookie set by :8443 is sent to :9999 on the same
        host — so hostname is exactly the line the session actually draws.
        """
        if not self._settings.auth_strict_origin or not origin:
            return True
        if origin == "null":  # sandboxed iframe or file:// — not our UI
            return False
        origin_host = (urlsplit(origin).hostname or "").lower()
        # urlsplit needs a scheme to find a hostname; a bare Host header has
        # none, so give it one rather than parsing the port off by hand.
        request_host = (urlsplit(f"//{host or ''}").hostname or "").lower()
        return bool(origin_host) and origin_host == request_host

    def status(self, claims: dict | None) -> AuthStatus:
        return AuthStatus(
            enabled=self.enabled,
            authenticated=claims is not None or not self.enabled,
            session_expires_at=claims.get("exp") if claims else None,
        )
