"""
Transport and abuse protections (§99).

Each test here corresponds to a specific way the platform was unsafe the moment
it stopped being a localhost toy, and two of them cover bugs found by running
the thing rather than reading it.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from throughline_api import security


@pytest.fixture()
def client():
    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    """Each test gets its own counters, so ordering cannot couple them."""
    monkeypatch.setattr(security, "_limiter", security.RateLimiter())


def test_secure_cookie_is_on_unless_the_deployment_says_local(monkeypatch):
    """
    The flag was hardcoded False, which is right on localhost and silently wrong
    anywhere else — the session token would travel in clear text with nothing in
    the interface saying so.
    """
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")
    assert security.session_cookie_kwargs()["secure"] is True

    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    assert security.session_cookie_kwargs()["secure"] is False

    # Absent configuration must not silently mean "hosted with no TLS".
    monkeypatch.delenv("THROUGHLINE_DEPLOYMENT", raising=False)
    assert security.session_cookie_kwargs()["secure"] is False
    assert security.session_cookie_kwargs()["httponly"] is True
    assert security.session_cookie_kwargs()["samesite"] == "strict"


def test_login_is_throttled_before_a_password_can_be_guessed(monkeypatch):
    # Stated explicitly: guessing is a threat from the network, so this is the
    # hosted case. The local case is relaxed on purpose and covered below.
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")
    limiter = security.RateLimiter()
    route = "/api/auth/login"
    limit = security.LIMITS[route]

    for _ in range(limit.requests):
        allowed, _ = limiter.check(key="1.2.3.4", route=route)
        assert allowed

    allowed, retry_after = limiter.check(key="1.2.3.4", route=route)
    assert not allowed
    assert retry_after > 0


def test_a_local_install_does_not_lock_the_researcher_out_of_their_own_machine(
        monkeypatch):
    """
    Ten attempts per five minutes is right when an attacker can reach the login
    and wrong when nobody can. Mistyping a long password three times should not
    cost a researcher access to their own corpus for five minutes.
    """
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    route = "/api/auth/login"
    limiter = security.RateLimiter()

    # Comfortably past the hosted ceiling, still allowed.
    for _ in range(security.LIMITS[route].requests + 1):
        allowed, _ = limiter.check(key="127.0.0.1", route=route)
        assert allowed

    # Relaxed is not unlimited: a runaway loop still meets a wall, because every
    # attempt costs 600k PBKDF2 rounds of this machine's CPU.
    for _ in range(security.LOCAL_LIMITS[route].requests):
        limiter.check(key="127.0.0.1", route=route)
    allowed, retry_after = limiter.check(key="127.0.0.1", route=route)
    assert not allowed
    assert retry_after > 0


def test_the_sandbox_limits_do_not_relax_on_a_local_install(monkeypatch):
    """
    The analysis limits are not about credentials. Each request starts a
    sandboxed subprocess, so a loop over them exhausts this machine whether or
    not anyone else can reach it — being local is not a reason to lift them.
    """
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    for route in ("/api/projects/{project_id}/discoveries",
                  "/api/projects/{project_id}/analyses"):
        assert security.limit_for(route) == security.LIMITS[route]
        assert route not in security.LOCAL_LIMITS

    # And the credential routes really are the ones that changed.
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")
    assert security.limit_for("/api/auth/login") == security.LIMITS["/api/auth/login"]
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    assert security.limit_for("/api/auth/login") == security.LOCAL_LIMITS["/api/auth/login"]


def test_throttling_is_per_client_not_global():
    """One noisy client must not lock everyone else out."""
    limiter = security.RateLimiter()
    route = "/api/auth/login"
    for _ in range(security.LIMITS[route].requests + 1):
        limiter.check(key="attacker", route=route)

    allowed, _ = limiter.check(key="researcher", route=route)
    assert allowed


def test_throttling_is_per_route():
    """Exhausting login must not block reading a project."""
    limiter = security.RateLimiter()
    for _ in range(security.LIMITS["/api/auth/login"].requests + 1):
        limiter.check(key="same", route="/api/auth/login")

    allowed, _ = limiter.check(key="same", route="/api/projects")
    assert allowed


def test_a_forwarded_header_is_ignored_unless_a_proxy_is_declared(monkeypatch):
    """
    Trusting X-Forwarded-For unconditionally would let a caller pick their own
    bucket and rotate past the limiter entirely.
    """
    class FakeClient:
        host = "10.0.0.1"

    class FakeRequest:
        headers = {"x-forwarded-for": "9.9.9.9"}
        client = FakeClient()

    monkeypatch.delenv("THROUGHLINE_TRUST_PROXY", raising=False)
    assert security.client_key(FakeRequest()) == "10.0.0.1"

    monkeypatch.setenv("THROUGHLINE_TRUST_PROXY", "true")
    assert security.client_key(FakeRequest()) == "9.9.9.9"


def test_security_headers_are_applied():
    from fastapi.responses import JSONResponse

    response = JSONResponse(content={})
    security._apply_headers(response)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_media_permissions_allow_only_same_origin():
    """Camera/voice features require permission, but embeds must never inherit it."""
    from fastapi.responses import JSONResponse

    response = JSONResponse(content={})
    security._apply_headers(response)
    policy = response.headers["Permissions-Policy"]

    assert "camera=(self)" in policy
    assert "microphone=(self)" in policy
    assert "camera=()" not in policy
    assert "microphone=()" not in policy
    assert "geolocation=()" in policy
    assert "payment=()" in policy
    assert "usb=()" in policy


def test_hsts_is_not_sent_from_a_local_install(monkeypatch):
    """
    HSTS from a plain-HTTP local install would pin the browser to a scheme that
    is not served, locking the researcher out of their own machine.
    """
    from fastapi.responses import JSONResponse

    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    response = JSONResponse(content={})
    security._apply_headers(response)
    assert "Strict-Transport-Security" not in response.headers

    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")
    hosted = JSONResponse(content={})
    security._apply_headers(hosted)
    assert "max-age=" in hosted.headers["Strict-Transport-Security"]


def test_a_throttled_request_answers_429_not_500(client, monkeypatch):
    """
    Found by running it. Raising HTTPException inside BaseHTTPMiddleware never
    reaches FastAPI's handlers, so the caller saw 500 — which reads as a server
    fault, so a client retries immediately instead of backing off.
    """
    # Enabled explicitly: it is off under pytest so that the rest of the suite
    # is not throttled by shared process-global counters.
    monkeypatch.setenv("THROUGHLINE_RATE_LIMIT", "on")
    # Hosted, so the strict login ceiling applies and 429 is reachable in a
    # dozen calls rather than a hundred.
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")

    codes = []
    for _ in range(security.LIMITS["/api/auth/login"].requests + 2):
        response = client.post("/api/auth/login",
                               json={"email": "x@y.z", "password": "wrong"})
        codes.append(response.status_code)

    assert 429 in codes
    assert 500 not in codes
    throttled = next(c for c in codes if c == 429)
    assert throttled == 429


def test_rate_limiting_is_off_under_pytest_by_default(monkeypatch):
    """
    The suite must not be throttled by its own traffic.

    Introducing the limiter broke ten unrelated tests for exactly this reason:
    process-global counters plus hundreds of calls from one host.
    """
    monkeypatch.delenv("THROUGHLINE_RATE_LIMIT", raising=False)
    assert security.rate_limiting_enabled() is False

    monkeypatch.setenv("THROUGHLINE_RATE_LIMIT", "on")
    assert security.rate_limiting_enabled() is True


# ---------------------------------------------------------------------------
# The script policy actually tightens
# ---------------------------------------------------------------------------

class _Response:
    """Just enough of a Response for the header pass to write into."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def _csp(monkeypatch, deployment: str) -> str:
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", deployment)
    response = _Response()
    security._apply_headers(response)  # noqa: SLF001 - the header pass itself
    return response.headers["Content-Security-Policy"]


def test_a_deployment_does_not_allow_inline_or_eval_scripts(monkeypatch):
    """The check that was silently never engaging.

    This was gated on NODE_ENV, which the API process never sets — it is a
    Python process, and the Next.js process that does set it has its own
    environment. The comparison was always `None != "production"`, so every
    deployment shipped 'unsafe-inline' and 'unsafe-eval' while the comment
    beside it said the policy tightened automatically.

    A security control that never engages is worse than an absent one, because
    nobody goes looking for it.
    """
    policy = _csp(monkeypatch, "production")
    assert "script-src 'self';" in policy
    assert "unsafe-inline" not in policy.split("style-src")[0]
    assert "unsafe-eval" not in policy


def test_a_local_install_still_allows_what_the_dev_build_needs(monkeypatch):
    # Next's development build genuinely requires both, so refusing them
    # locally would break the product for everyone running it the normal way.
    policy = _csp(monkeypatch, "local")
    assert "'unsafe-inline' 'unsafe-eval'" in policy


def test_node_env_no_longer_decides_anything(monkeypatch):
    """Setting NODE_ENV must not loosen a deployment's policy.

    Kept because the obvious "fix" for a future report of a broken dev build is
    to reinstate the old variable, which would restore the bug in a form that
    looks deliberate.
    """
    monkeypatch.setenv("NODE_ENV", "development")
    assert "unsafe-eval" not in _csp(monkeypatch, "production")


# ---------------------------------------------------------------------------
# Through the middleware, where the limits actually have to be found (T167)
# ---------------------------------------------------------------------------

def _limiting_on(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_RATE_LIMIT", "on")
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")


def test_a_template_keyed_limit_applies_to_real_requests(monkeypatch, client):
    """
    The limit for starting a discovery is keyed by its route template, and the
    middleware looked it up by the request's *concrete* path — `scope["route"]`
    is set by the router only after middleware has run, so it was always None.
    No path matches a template, so the sandbox limits never applied. The tests
    above call `limit_for` with the template directly and never met the
    middleware.
    """
    _limiting_on(monkeypatch)
    allowed = security.LIMITS["/api/projects/{project_id}/discoveries"].requests

    answers = [client.post(f"/api/projects/prj_{i}/discoveries", json={}).status_code
               for i in range(allowed + 1)]

    assert 429 not in answers[:allowed], answers
    assert answers[allowed] == 429, answers


def test_a_limit_is_per_caller_not_per_project(monkeypatch, client):
    """
    Each request above names a different project. Counted per concrete path, a
    caller rotating project ids would never meet a limit at all — the bucket is
    the caller and the route, whatever id the path carries.
    """
    _limiting_on(monkeypatch)
    route = "/api/projects/{project_id}/analyses"
    allowed = security.LIMITS[route].requests

    for i in range(allowed):
        client.post(f"/api/projects/prj_rotate_{i}/analyses", json={})
    assert client.post("/api/projects/prj_brand_new/analyses", json={}).status_code == 429


def test_guessing_the_current_password_is_throttled(monkeypatch, client):
    """
    `POST /api/auth/password` checks the current password, so a stolen session
    could otherwise try one guess after another at the default 600 a minute.
    """
    _limiting_on(monkeypatch)
    assert "/api/auth/password" in security.LIMITS
    allowed = security.LIMITS["/api/auth/password"].requests

    answers = [client.post("/api/auth/password", json={
        "current_password": f"guess-{i:012d}", "new_password": "x" * 12}).status_code
               for i in range(allowed + 1)]
    assert answers[allowed] == 429, answers


def test_signing_up_from_the_network_is_throttled(monkeypatch, client):
    """
    Sign-up from the network can be opened now (T166), and an open sign-up with
    only the default limit is an account-creation loop.
    """
    _limiting_on(monkeypatch)
    assert "/api/auth/register" in security.LIMITS
    allowed = security.LIMITS["/api/auth/register"].requests

    answers = [client.post("/api/auth/register", json={
        "email": f"flood{i}@lab.local", "display_name": "F", "password": "x" * 12}).status_code
               for i in range(allowed + 1)]
    assert answers[allowed] == 429, answers


def test_the_security_middleware_is_registered_once():
    """
    It was registered three times, so each request was counted three times and
    every limit was a third of its stated size (T167).
    """
    from throughline_api.app import app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert names.count("SecurityMiddleware") == 1, names


def test_a_hosted_sign_in_allows_the_attempts_it_says(monkeypatch, client):
    """Counted once per request: ten wrong passwords before the eleventh is refused."""
    _limiting_on(monkeypatch)
    allowed = security.LIMITS["/api/auth/login"].requests

    answers = [client.post("/api/auth/login", json={
        "email": "nobody@lab.local", "password": f"wrong-{i:012d}"}).status_code
               for i in range(allowed + 1)]

    assert 429 not in answers[:allowed], answers
    assert answers[allowed] == 429, answers


def test_reading_a_list_is_not_held_to_the_limit_on_starting_work(monkeypatch, client):
    """
    `/api/projects/{project_id}/analyses` is both "start an analysis" (sixty an
    hour) and the list the interface polls. Keyed by template alone, resolving
    templates would have throttled the list — found only once they resolved.
    """
    _limiting_on(monkeypatch)
    allowed = security.LIMITS["/api/projects/{project_id}/analyses"].requests

    answers = [client.get("/api/projects/prj_1/analyses").status_code
               for _ in range(allowed + 5)]

    assert 429 not in answers, answers


def test_reads_do_not_spend_the_budget_for_starting_work(monkeypatch, client):
    _limiting_on(monkeypatch)
    allowed = security.LIMITS["/api/projects/{project_id}/analyses"].requests

    for _ in range(allowed + 5):
        client.get("/api/projects/prj_1/analyses")

    assert client.post("/api/projects/prj_1/analyses", json={}).status_code != 429
