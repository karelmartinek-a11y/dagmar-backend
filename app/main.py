from __future__ import annotations

import time
from typing import Any, Protocol, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from app.api.v1.admin_attendance import router as admin_attendance_router
from app.api.v1.admin_auth import router as admin_auth_router
from app.api.v1.admin_export import router as admin_export_router
from app.api.v1.admin_settings import router as admin_settings_router
from app.api.v1.admin_shift_plan import router as admin_shift_plan_router
from app.api.v1.admin_smtp import router as admin_smtp_router
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.attendance import router as attendance_router
from app.api.v1.portal_auth import router as portal_auth_router
from app.brand.brand import APP_NAME_LONG
from app.config import Settings, get_settings
from app.security.rate_limit import init_rate_limiting, limiter


class _LimiterWithDefaults(Protocol):
    default_limits: list[str]


def _now_ms() -> int:
    return int(time.time() * 1000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=APP_NAME_LONG,
        version="1.0.0",
        docs_url=None if settings.disable_docs else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.disable_docs else "/api/openapi.json",
    )

    # --- Middleware order matters: rate-limit early, sessions before endpoints.
    if settings.rate_limit_enabled:
        if settings.rate_limit_default_per_minute:
            limiter_with_defaults = cast(_LimiterWithDefaults, limiter)
            limiter_with_defaults.default_limits = [f"{settings.rate_limit_default_per_minute}/minute"]
        init_rate_limiting(app)

    # Admin session cookie.
    # NOTE: Secure cookies require HTTPS; in local dev you can set cookie_secure=false.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        # Use a dedicated session cookie to avoid clashing with the admin auth cookie.
        session_cookie=f"{settings.admin_session_cookie}_store",
        https_only=settings.cookie_secure,
        same_site=settings.cookie_samesite,
        max_age=settings.session_max_age_seconds,
    )

    # CORS (only needed for local dev; in prod we keep strict same-origin)
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"] ,
        )

    @app.middleware("http")
    async def request_id_and_timing(request: Request, call_next):
        start_ms = _now_ms()
        response = await call_next(request)
        dur_ms = _now_ms() - start_ms
        response.headers["X-Request-Duration-Ms"] = str(dur_ms)
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_request",
                        "message": "Neplatný požadavek.",
                        "details": exc.errors(),
                    }
                },
            )
        raise exc

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/version", include_in_schema=False)
    async def version() -> dict[str, Any]:
        return {
            "backend_deploy_tag": settings.deploy_tag,
            "environment": settings.environment,
        }

    # Routers already carry full prefixes ("/api/v1/..."), so include without extra prefixes
    # to avoid duplicate paths like "/api/v1/api/v1/...".
    app.include_router(attendance_router)

    app.include_router(admin_auth_router, tags=["admin"])
    app.include_router(admin_export_router, tags=["admin"])
    app.include_router(admin_attendance_router, tags=["admin"])
    app.include_router(admin_shift_plan_router, tags=["admin"])
    app.include_router(admin_settings_router, tags=["admin"])
    app.include_router(admin_users_router, tags=["admin"])
    app.include_router(admin_smtp_router, tags=["admin"])
    app.include_router(portal_auth_router, tags=["portal"])

    # Consistent JSON error for unhandled exceptions in API paths.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Do not leak details to client.
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "Internal server error"}},
            )
        raise exc

    return app


app = create_app()
