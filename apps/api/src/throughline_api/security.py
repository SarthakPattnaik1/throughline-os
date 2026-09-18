"""
Transport and abuse protections.

Everything here is about the gap between "runs on my laptop" and "reachable by
anything else". Locally most of it is unnecessary; the moment the API binds to
anything but loopback, each of these is the difference between a research
corpus that is private and one that is not.

The design rule throughout: **secure by default, relaxed only by an explicit
opt-out.**
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi.responses import JSONResponse
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match


def deployment_is_local() -> bool:
    return os.environ.get("THROUGHLINE_DEPLOYMENT", "local").lower() == "local"


def session_cookie_kwargs() -> dict[str, object]:
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": not deployment_is_local(),
        "path": "/",
    }


@dataclass(frozen=True, slots=True)
class Limit:
    requests: int
    window_seconds: int


LIMITS: dict[str, Limit] = {
    "/api/auth/login": Limit(10, 300),
    "/api/auth/setup": Limit(5, 3600),
    "/api/auth/password": Limit(10, 300),
    "/api/auth/register": Limit(10, 3600),
    "/api/projects/{project_id}/discoveries": Limit(20, 3600),
    "/api/projects/{project_id}/analyses": Limit(60, 3600),
    "__default__": Limit(600, 60),
}

LOCAL_LIMITS: dict[str, Limit] = {
    "/api/auth/login": Limit(100, 300),
    "/api/auth/setup": Limit(30, 3600),
    "/api/auth/password": Limit(100, 300),
    "/api/auth/register": Limit(30, 3600),
}


def limit_for(route: str) -> Limit:
    if deployment_is_local():
        relaxed = LOCAL_LIMITS.get(route)
        if relaxed is not None:
            return relaxed
    return LIMITS.get(route, LIMITS["__default__"])


class RateLimiter:
    """A fixed-window counter held in memory for the single-node deployment."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, *, key: str, route: str) -> tuple[bool, int]:
        limit = limit_for(route)
        now = time.monotonic()
        bucket = self._hits[(key, route)]
        cutoff = now - limit.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit.requests:
            retry_after = int(bucket[0] + limit.window_seconds - now) + 1
            return False, max(retry_after, 1)
        bucket.append(now)
        return True, 0


def rate_limiting_enabled() -> bool:
    setting = os.environ.get("THROUGHLINE_RATE_LIMIT", "").lower()
    if setting in ("off", "0", "false", "disabled"):
        return False
    if setting in ("on", "1", "true", "enabled"):
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


_limiter = RateLimiter()


def client_key(request: Request) -> str:
    if os.environ.get("THROUGHLINE_TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def route_template(request: Request) -> str:
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


_READS = frozenset({"GET", "HEAD", "OPTIONS"})


def limit_key(request: Request) -> str:
    template = route_template(request)
    return f"{request.method} {template}" if request.method in _READS else template


def _generic_internal_error() -> JSONResponse:
    """The only body an unhandled 500 is allowed to expose to a caller.

    Domain errors and explicit 4xx/502/503 responses keep their actionable
    messages. A raw 500, however, can contain exception text with local paths,
    SQL details, dependency internals or other machine information. The app's
    legacy exception handler still creates such a response; this outer security
    boundary deliberately replaces that body before it leaves the process.
    """
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An internal error occurred. Check the server log for details.",
        },
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Rate limiting, safe error egress, and browser response protections."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        route_path = limit_key(request)
        allowed, retry_after = (
            _limiter.check(key=client_key(request), route=route_path)
            if rate_limiting_enabled() else (True, 0))
        if not allowed:
            throttled = JSONResponse(
                status_code=429,
                content={"detail": f"Too many requests to {route_path}. "
                                   f"Try again in {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )
            _apply_headers(throttled)
            return throttled

        try:
            response: Response = await call_next(request)
        except Exception:
            # Server logging remains the place for the traceback. Never reflect
            # the exception object back across the HTTP boundary.
            response = _generic_internal_error()

        # FastAPI can turn an unhandled exception into a 500 response inside
        # `call_next`; sanitize that legacy path too. Expected 502/503 domain
        # failures are intentionally untouched because their messages tell the
        # researcher what external capability is unavailable.
        if response.status_code == 500:
            response = _generic_internal_error()

        _apply_headers(response)
        return response


def _apply_headers(response: Response) -> None:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=(), usb=()")
    headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; "
        + ("script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
           if deployment_is_local() else "script-src 'self'; ")
        + "style-src 'self' 'unsafe-inline'; "
          "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
          "form-action 'self'")
    if not deployment_is_local():
        headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains")


__all__ = ["LIMITS", "LOCAL_LIMITS", "Limit", "RateLimiter", "SecurityMiddleware",
           "client_key", "deployment_is_local", "limit_for",
           "session_cookie_kwargs"]
