"""§1.3a — authentication.

The spec's §1.3 listed auth as a non-goal for a single-user local/LAN tool.
That was a bet on where the box sits, and this file is what covers the bet
coming off: the gate exists, it is closed by default once a password is
configured, and it stays open — unchanged, visibly — when one is not.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import PasswordHashError, hash_password, parse_hash, verify_password
from app.auth.routes import reset_auth_service
from app.auth.service import (
    THROTTLE_FREE_ATTEMPTS,
    AuthService,
    LoginThrottle,
    LoginThrottled,
)
from app.auth.sessions import InvalidSession, derive_secret, issue, verify
from app.config import get_settings
from app.main import app

PASSWORD = "a-password-long-enough-to-matter"


@pytest.fixture
def open_client():
    """The deployment as it has always been: no password configured."""
    settings = get_settings()
    settings.auth_password_hash = ""
    settings.api_token = ""
    reset_auth_service()
    yield TestClient(app)
    reset_auth_service()


@pytest.fixture
def secured():
    """A configured deployment. Yields (client, settings) so a test can
    adjust one setting and rebuild the service."""
    settings = get_settings()
    settings.auth_password_hash = hash_password(PASSWORD)
    settings.api_token = ""
    settings.auth_strict_origin = True
    reset_auth_service()
    yield TestClient(app), settings
    settings.auth_password_hash = ""
    settings.api_token = ""
    reset_auth_service()


# --- password hashing ------------------------------------------------------


def test_hash_verifies_and_is_salted():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second, "a repeated hash of one password must not repeat"
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


def test_wrong_password_is_rejected():
    assert not verify_password("nearly-" + PASSWORD, hash_password(PASSWORD))


def test_hash_carries_its_own_parameters():
    # The point of the self-describing format: raising the defaults later
    # must not invalidate a hash generated under the old ones.
    weak = hash_password(PASSWORD, n=1024)
    assert weak.startswith("scrypt:1024:")
    assert verify_password(PASSWORD, weak)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-hash",
        "bcrypt:16384:8:1:c2FsdA==:aGFzaA==",  # right shape, wrong algorithm
        "scrypt:16384:8:1:c2FsdA==",  # truncated
        "scrypt:16385:8:1:c2FsdA==:aGFzaA==",  # n not a power of two
        "scrypt:16384:8:1:not base64!:aGFzaA==",
    ],
)
def test_malformed_hashes_are_configuration_errors(bad):
    # Not "wrong password": a typo in .env has to read as a typo in .env.
    with pytest.raises(PasswordHashError):
        parse_hash(bad)


def test_hash_is_safe_to_paste_into_a_dotenv_file():
    """The hash's destination is a `.env` read by Docker Compose, which
    interpolates `$` in values. A PHC-shaped `scrypt$16384$...` arrives at
    the container with its parameters blanked, and the symptom is "incorrect
    password" for the right password — so the separator cannot be `$`."""
    hashed = hash_password(PASSWORD)
    assert "$" not in hashed
    # Nor anything else compose or a shell would treat as syntax.
    assert not set(hashed) & set("$`\\\"'\n \t")


def test_the_older_dollar_separated_spelling_still_verifies():
    # An install that generated a hash before the separator changed must not
    # lock its owner out on upgrade.
    legacy = hash_password(PASSWORD).replace(":", "$", 5)
    assert "$" in legacy
    assert verify_password(PASSWORD, legacy)


def test_empty_password_refused_at_generation():
    with pytest.raises(ValueError):
        hash_password("")


# --- session tokens --------------------------------------------------------


def test_token_round_trips():
    secret = derive_secret(hash_password(PASSWORD))
    claims = verify(secret, issue(secret, ttl_seconds=60))
    assert claims["sub"] == "owner"
    assert claims["exp"] > claims["iat"]


def test_token_from_another_secret_is_rejected():
    mine = derive_secret(hash_password(PASSWORD))
    theirs = derive_secret(hash_password("some other password"))
    with pytest.raises(InvalidSession):
        verify(mine, issue(theirs, ttl_seconds=60))


def test_expired_token_is_rejected():
    secret = derive_secret(hash_password(PASSWORD))
    token = issue(secret, ttl_seconds=60)
    with pytest.raises(InvalidSession):
        verify(secret, token, now=time.time() + 61)


def test_tampered_payload_is_rejected():
    secret = derive_secret(hash_password(PASSWORD))
    payload, signature = issue(secret, ttl_seconds=60).split(".")
    forged = payload[:-2] + ("aa" if not payload.endswith("aa") else "bb")
    with pytest.raises(InvalidSession):
        verify(secret, f"{forged}.{signature}")


def test_changing_the_password_invalidates_sessions():
    """The property the derived-secret design exists for: rotating the
    password in .env logs every browser out, with nothing to revoke."""
    before = derive_secret(hash_password(PASSWORD))
    token = issue(before, ttl_seconds=3600)
    after = derive_secret(hash_password("the new password entirely"))
    with pytest.raises(InvalidSession):
        verify(after, token)


def test_explicit_secret_survives_a_password_change():
    # The documented escape hatch for anyone who wants the opposite.
    shared = "an-explicitly-configured-signing-secret"
    before = derive_secret(hash_password(PASSWORD), shared)
    after = derive_secret(hash_password("a different password"), shared)
    assert verify(after, issue(before, ttl_seconds=60))["sub"] == "owner"


# --- the throttle ----------------------------------------------------------


def test_throttle_allows_a_few_then_backs_off():
    throttle = LoginThrottle()
    for _ in range(THROTTLE_FREE_ATTEMPTS):
        throttle.check(now=0.0)
        throttle.record_failure(now=0.0)
    throttle.check(now=0.0)  # the free attempts are genuinely free

    throttle.record_failure(now=0.0)
    with pytest.raises(LoginThrottled) as caught:
        throttle.check(now=0.0)
    assert caught.value.retry_after >= 1

    # And it is a delay, not a lockout — the owner who fat-fingered it waits.
    throttle.check(now=60.0)


def test_a_success_clears_the_backoff():
    throttle = LoginThrottle()
    for _ in range(THROTTLE_FREE_ATTEMPTS + 3):
        throttle.record_failure(now=0.0)
    throttle.record_success()
    throttle.check(now=0.0)


# --- the gate --------------------------------------------------------------


def test_open_deployment_is_unchanged(open_client):
    """No password configured: every endpoint answers as it always did, and
    the session endpoint says out loud that the door is open."""
    assert open_client.get("/health").status_code == 200
    assert open_client.get("/api/auth/session").json() == {
        "enabled": False,
        "authenticated": True,
        "session_expires_at": None,
    }
    # A route that needs no data still has to be reachable without a cookie.
    assert open_client.get("/api/charts/axes").status_code == 200


def test_login_is_refused_when_no_password_is_configured(open_client):
    # Rather than handing out a cookie that means nothing and letting the UI
    # believe it is protected.
    assert open_client.post("/api/auth/login", json={"password": "anything"}).status_code == 400


def test_protected_endpoints_401_without_a_session(secured):
    client, _ = secured
    for path in ("/api/settings", "/api/charts/axes", "/api/trades", "/api/sync/health"):
        assert client.get(path).status_code == 401, path


def test_health_stays_public(secured):
    """The container healthcheck and the compose dependency gate call it
    with no credential, and it reports liveness only."""
    client, _ = secured
    assert client.get("/health").status_code == 200


def test_docs_are_not_public(secured):
    client, _ = secured
    assert client.get("/openapi.json").status_code == 401


def test_login_then_reach_the_api(secured, tmp_path, monkeypatch):
    client, settings = secured
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)

    assert client.get("/api/auth/session").json()["authenticated"] is False

    bad = client.post("/api/auth/login", json={"password": "wrong"})
    assert bad.status_code == 401
    assert "Incorrect" in bad.json()["detail"]

    good = client.post("/api/auth/login", json={"password": PASSWORD})
    assert good.status_code == 200
    assert good.json()["authenticated"] is True

    cookie = good.headers["set-cookie"]
    assert "HttpOnly" in cookie, "the token must be out of reach of any script"
    assert "samesite=lax" in cookie.lower()

    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.get("/api/charts/axes").status_code == 200


def test_logout_closes_the_session(secured):
    client, _ = secured
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.get("/api/charts/axes").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/charts/axes").status_code == 401
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_a_forged_cookie_does_not_get_in(secured):
    client, _ = secured
    client.cookies.set("settled_session", "eyJzdWIiOiJvd25lciJ9.not-a-real-signature")
    assert client.get("/api/charts/axes").status_code == 401


def test_password_never_appears_in_a_response(secured):
    client, _ = secured
    body = client.post("/api/auth/login", json={"password": PASSWORD}).text
    assert PASSWORD not in body
    # Nor does the hash it was checked against.
    assert "scrypt:" not in body


# --- the API token ---------------------------------------------------------


def test_bearer_token_works_for_the_host_cron_path(secured):
    """§3.8's documented `curl -X POST .../api/sync/run` has no cookie jar.
    Without this, turning auth on would silently break external sync."""
    client, settings = secured
    settings.api_token = "a-token-for-the-cron-job"
    reset_auth_service()

    assert client.get("/api/sync/health", headers={"Authorization": "Bearer a-token-for-the-cron-job"}).status_code == 200
    assert client.get("/api/sync/health", headers={"Authorization": "Bearer nearly-right"}).status_code == 401
    assert client.get("/api/sync/health", headers={"Authorization": "Basic a-token-for-the-cron-job"}).status_code == 401


def test_no_token_configured_means_no_bearer_path(secured):
    client, settings = secured
    settings.api_token = ""
    reset_auth_service()
    # Notably including the empty string, which must not match an empty header.
    assert client.get("/api/sync/health", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get("/api/sync/health", headers={"Authorization": "Bearer anything"}).status_code == 401


# --- cross-origin ----------------------------------------------------------


def test_cross_origin_writes_are_rejected(secured):
    client, _ = secured
    client.post("/api/auth/login", json={"password": PASSWORD})
    blocked = client.post("/api/sync/run", headers={"Origin": "http://attacker.example"})
    assert blocked.status_code == 403


def test_same_origin_writes_pass_the_check(secured, tmp_path, monkeypatch):
    client, settings = secured
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    client.post("/api/auth/login", json={"password": PASSWORD})
    # Not 403 is the assertion; 400 (no Flex credentials) is a fine outcome
    # and means the request reached the route.
    assert client.post(
        "/api/sync/run", headers={"Origin": "http://testserver", "Host": "testserver"}
    ).status_code != 403


def test_origin_check_ignores_the_port_nginx_strips(secured):
    """The bug this exists to prevent: nginx forwards `Host: $host`, which
    has no port, while the browser's Origin has one. Comparing them whole
    rejects every write on the ordinary TLS deployment — a 403 on saving a
    note, with nothing in the UI to explain it."""
    _, settings = secured
    service = AuthService(settings)
    assert service.origin_is_acceptable("https://settled.lan:8443", "settled.lan")
    assert service.origin_is_acceptable("http://192.168.1.40:8080", "192.168.1.40")
    # Ports being ignored is not a hole: a cookie set by :8443 is sent to
    # :9999 on the same host anyway, so hostname is the real boundary.
    assert not service.origin_is_acceptable("https://attacker.example", "settled.lan")
    # And a suffix is not a match.
    assert not service.origin_is_acceptable("https://settled.lan.evil.example", "settled.lan")


def test_origin_check_can_be_turned_off(secured):
    _, settings = secured
    settings.auth_strict_origin = False
    try:
        assert AuthService(settings).origin_is_acceptable("https://attacker.example", "settled.lan")
    finally:
        settings.auth_strict_origin = True


def test_cors_wraps_the_gate(secured):
    """A 401 has to carry CORS headers, or a cross-origin dev frontend sees
    an opaque CORS failure in the console instead of the sign-in it needs.
    Starlette makes the most recently added middleware outermost, so this is
    really an assertion about registration order in main.py."""
    client, _ = secured
    rejected = client.get("/api/settings", headers={"Origin": "http://localhost:5173"})
    assert rejected.status_code == 401
    assert rejected.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_reads_are_never_blocked_by_origin(secured):
    client, _ = secured
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.get("/api/charts/axes", headers={"Origin": "http://attacker.example"}).status_code == 200


def test_missing_origin_passes(secured, tmp_path, monkeypatch):
    """curl sends none. Rejecting on absence would break the documented
    host-cron path for no security gain — a browser always sends one on a
    cross-site write."""
    client, settings = secured
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.post("/api/sync/run").status_code != 403


# --- cookie flags and refresh ---------------------------------------------


def test_cookie_is_secure_behind_a_tls_proxy():
    settings = get_settings()
    settings.auth_password_hash = hash_password(PASSWORD)
    settings.cookie_secure = "auto"
    reset_auth_service()
    try:
        service = AuthService(settings)
        # nginx terminates TLS and the app only ever sees plain HTTP, so the
        # forwarded header is the only evidence available.
        assert service.cookie_is_secure("http", "https") is True
        assert service.cookie_is_secure("http", None) is False
        assert service.cookie_is_secure("http", "https, http") is True

        settings.cookie_secure = "true"
        assert AuthService(settings).cookie_is_secure("http", None) is True
        settings.cookie_secure = "false"
        assert AuthService(settings).cookie_is_secure("https", "https") is False
    finally:
        settings.cookie_secure = "auto"
        settings.auth_password_hash = ""
        reset_auth_service()


def test_session_refreshes_past_its_half_life():
    settings = get_settings()
    settings.auth_password_hash = hash_password(PASSWORD)
    settings.session_ttl_hours = 10
    reset_auth_service()
    try:
        service = AuthService(settings)
        now = time.time()
        fresh = {"exp": now + service.ttl_seconds}
        assert service.should_refresh(fresh, now) is False
        # Past halfway, a browser in daily use gets a new cookie rather than
        # being logged out on a fixed schedule.
        aging = {"exp": now + (service.ttl_seconds / 2) - 1}
        assert service.should_refresh(aging, now) is True
    finally:
        settings.session_ttl_hours = 24 * 14
        settings.auth_password_hash = ""
        reset_auth_service()


def test_a_broken_hash_is_caught_at_construction():
    """Startup, not first login. Someone typing the right password should
    never be told it is wrong because .env has a typo in it."""
    settings = get_settings()
    settings.auth_password_hash = "scrypt:oops"
    try:
        with pytest.raises(PasswordHashError):
            AuthService(settings)
    finally:
        settings.auth_password_hash = ""
        reset_auth_service()
