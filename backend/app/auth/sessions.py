"""Signed session tokens.

Stateless by design. A server-side session table would mean the owner is
logged out by every `docker compose up -d`, which on a tool that gets
restarted for an `.env` edit is a papercut with no security benefit at this
scale — one user, one browser, no revocation UI to build.

    <payload-b64url>.<hmac-sha256-b64url>

The payload is JSON and readable by anyone holding the cookie. That is fine:
it carries an issue time and an expiry, nothing secret. The signature is
what makes it unforgeable.
"""

import base64
import hashlib
import hmac
import json
import time

TOKEN_VERSION = 1
_SECRET_DOMAIN = b"settled.session-key.v1"


class InvalidSession(Exception):
    """Missing, malformed, mis-signed or expired — all indistinguishable to
    the caller on purpose, so a probe learns nothing from which it got."""


def derive_secret(password_hash: str, explicit_secret: str = "") -> bytes:
    """The signing key.

    Derived from the password hash by default, which gives two properties
    for free: it is unique per install (the hash carries a random salt), and
    changing the password invalidates every outstanding session — the thing
    you actually want a password change to do.

    An explicit SETTLED_AUTH_SECRET overrides it, for a deployment that
    wants sessions to survive a password rotation, or two replicas to share
    them.
    """
    material = explicit_secret.encode("utf-8") if explicit_secret else password_hash.encode("utf-8")
    return hashlib.sha256(_SECRET_DOMAIN + b"|" + material).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def issue(secret: bytes, *, ttl_seconds: int, subject: str = "owner", now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = _b64(
        json.dumps(
            {"v": TOKEN_VERSION, "sub": subject, "iat": issued, "exp": issued + ttl_seconds},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return f"{payload}.{_sign(secret, payload)}"


def verify(secret: bytes, token: str, *, now: float | None = None) -> dict:
    """Return the payload, or raise InvalidSession.

    The signature is checked before the payload is parsed, so untrusted JSON
    is never handed to the decoder on the strength of the cookie alone.
    """
    if not token or token.count(".") != 1:
        raise InvalidSession("malformed token")
    payload, signature = token.split(".", 1)
    if not hmac.compare_digest(_sign(secret, payload), signature):
        raise InvalidSession("bad signature")
    try:
        claims = json.loads(_unb64(payload))
    except Exception as exc:  # noqa: BLE001 — a signed-but-unparseable payload is still invalid
        raise InvalidSession(f"unreadable payload: {exc}") from exc
    if not isinstance(claims, dict) or claims.get("v") != TOKEN_VERSION:
        raise InvalidSession("unrecognized token version")
    expires = claims.get("exp")
    if not isinstance(expires, int):
        raise InvalidSession("missing expiry")
    if (now if now is not None else time.time()) >= expires:
        raise InvalidSession("expired")
    return claims
