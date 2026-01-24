"""Rate limiting for DAGMAR backend.

Requirements (Section A.6 + B):
- Rate limit at minimum for:
  - admin login
  - instance status polling
  - instance claim-token polling

Implementation notes:
- We implement an in-process, per-worker limiter using SlowAPI.
- For multi-worker deployments, consider a shared backend (e.g. Redis). This project
  intentionally avoids external third-party services; a local Redis could be added,
  but is not required by the spec.

This module exposes:
- init_rate_limiting(app): attach SlowAPI limiter to FastAPI app
- limit_* dependencies: helper decorators to apply limits on endpoints

We key by client IP (from Nginx via X-Forwarded-For). Nginx config MUST set:
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Real-IP $remote_addr;

"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address


def _real_ip_keyfunc(request: Request) -> str:
    """Return best-effort real client IP.

    We prefer X-Real-IP (set by Nginx) and fall back to SlowAPI's get_remote_address.
    """
    x_real = request.headers.get("x-real-ip")
    if x_real:
        return x_real.strip()

    # If behind reverse proxy, SlowAPI can read request.client.host (may be Nginx).
    # Nginx should pass X-Real-IP; this is a fallback.
    return get_remote_address(request)


# Global limiter instance used by the app
limiter = Limiter(
    key_func=_real_ip_keyfunc,
    default_limits=[],  # no global default; apply per-route
    headers_enabled=True,
)


def init_rate_limiting(app) -> None:
    """Attach SlowAPI middleware and exception handler."""

    @app.exception_handler(RateLimitExceeded)
    def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        # SlowAPI provides proper 429; we return JSON via FastAPI default.
        # Returning None lets SlowAPI handle? We provide minimal JSON ourselves.
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Příliš mnoho požadavků. Zkuste to prosím později.",
                }
            },
        )

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)


class DagmarRateLimits:
    """Centralized limits (string syntax as SlowAPI expects)."""

    # Admin login brute-force protection
    ADMIN_LOGIN = "5/minute"

    # Instance polling endpoints (status & claim-token)
    INSTANCE_STATUS = "30/minute"
    INSTANCE_CLAIM_TOKEN = "20/minute"

    # Attendance operations: keep reasonably high but still bounded
    ATTENDANCE_GET = "120/minute"
    ATTENDANCE_PUT = "120/minute"


def limit_admin_login(route_limit: Optional[str] = None):
    """Decorator for admin login endpoint."""

    return limiter.limit(route_limit or DagmarRateLimits.ADMIN_LOGIN)


def limit_instance_status(route_limit: Optional[str] = None):
    """Decorator for instance status polling endpoint."""

    return limiter.limit(route_limit or DagmarRateLimits.INSTANCE_STATUS)


def limit_instance_claim_token(route_limit: Optional[str] = None):
    """Decorator for instance claim-token polling endpoint."""

    return limiter.limit(route_limit or DagmarRateLimits.INSTANCE_CLAIM_TOKEN)


def limit_attendance_get(route_limit: Optional[str] = None):
    return limiter.limit(route_limit or DagmarRateLimits.ATTENDANCE_GET)


def limit_attendance_put(route_limit: Optional[str] = None):
    return limiter.limit(route_limit or DagmarRateLimits.ATTENDANCE_PUT)


def rate_limit(spec: str):
    """Generic helper matching legacy name."""
    return limiter.limit(spec)
