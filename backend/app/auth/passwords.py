"""Password hashing for the single owner account (§1.3a).

Stdlib only, deliberately. `hashlib.scrypt` has been in Python since 3.6 and
is a memory-hard KDF in the same class as argon2; adding passlib or
argon2-cffi would buy a marginally better KDF at the cost of a C extension
in a deployment whose stated goal is to still build in three years.

The stored form is a single self-describing string, so the parameters travel
with the hash and an old hash keeps verifying after the defaults are raised:

    scrypt:16384:8:1:<salt-b64>:<key-b64>

Colons, not the `$` of the PHC convention this otherwise imitates. The hash's
destination is a `.env` file read by Docker Compose, which interpolates `$`
in values: a PHC-shaped hash arrives at the container with `$16384` and
friends replaced by empty strings, and the failure it produces is the exact
one this module exists to rule out — "incorrect password" for someone typing
the right one. Base64's alphabet has no colon in it, so the split is
unambiguous. `$` is still accepted on the way in, for any hash generated
before this and pasted somewhere it survived.
"""

import base64
import hashlib
import hmac
import secrets

# n=2**14, r=8, p=1 is the "interactive login" parameter set: ~16MB and on
# the order of 50-100ms on a NUC-class box. Slow enough that an offline
# attack on a leaked hash is expensive, fast enough that a login is not.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# hashlib.scrypt refuses to allocate past OpenSSL's default maxmem, which is
# below what n=16384, r=8 needs (128 * n * r = 16MB). Ask for it explicitly
# rather than lowering n until the default fits.
MAXMEM = 64 * 1024 * 1024


class PasswordHashError(ValueError):
    """A stored hash that cannot be parsed. Treated as a configuration error
    rather than a failed login: the difference matters, because the second
    reads as "wrong password" to someone who typed the right one."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001 — any decode failure is the same failure
        raise PasswordHashError(f"not valid base64: {exc}") from exc


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """Derive a storable hash. The salt is fresh per call, so hashing the
    same password twice gives two different strings — both valid."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=MAXMEM
    )
    return f"scrypt:{n}:{r}:{p}:{_b64(salt)}:{_b64(key)}"


def parse_hash(stored: str) -> tuple[int, int, int, bytes, bytes]:
    """Split a stored hash, raising PasswordHashError on anything malformed.

    Called at startup as well as at login, so a typo in the environment is
    reported when the container boots rather than the first time someone
    tries to sign in.
    """
    text = stored.strip()
    parts = text.split(":")
    if len(parts) != 6 or parts[0] != "scrypt":
        # A `$`-separated hash is the older spelling, still honoured so an
        # existing deployment does not lock its owner out on upgrade.
        parts = text.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        raise PasswordHashError(
            "expected a hash of the form scrypt:n:r:p:salt:key — "
            "generate one with `python -m app.auth`"
        )
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError as exc:
        raise PasswordHashError(f"non-numeric scrypt parameters: {exc}") from exc
    if n <= 1 or n & (n - 1):
        raise PasswordHashError("scrypt n must be a power of two greater than 1")
    if r < 1 or p < 1:
        raise PasswordHashError("scrypt r and p must be positive")
    salt, key = _unb64(parts[4]), _unb64(parts[5])
    if not salt or not key:
        raise PasswordHashError("empty salt or key")
    return n, r, p, salt, key


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify. A malformed hash propagates as
    PasswordHashError; only a genuine mismatch returns False."""
    n, r, p, salt, expected = parse_hash(stored)
    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=MAXMEM,
    )
    return hmac.compare_digest(candidate, expected)
