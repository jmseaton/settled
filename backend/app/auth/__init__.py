"""§1.3a — single-owner authentication.

The spec's §1.3 listed "multi-user, auth, billing, sharing" as non-goals on
the grounds of a single-user local/LAN deployment. Multi-user and sharing
remain non-goals; a password does not. The change of mind is narrow and
worth stating: nothing here grows a user table, a role, or an invite. There
is one owner, one password, one session cookie.
"""

from app.auth.service import (
    PUBLIC_PATHS,
    SESSION_COOKIE,
    AuthService,
    AuthStatus,
    LoginThrottled,
)

__all__ = [
    "PUBLIC_PATHS",
    "SESSION_COOKIE",
    "AuthService",
    "AuthStatus",
    "LoginThrottled",
]
