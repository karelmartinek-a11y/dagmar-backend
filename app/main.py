from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from app.api.integration_common import (
    INTEGRATION_NAMESPACE,
    IntegrationError,
    ensure_request_id,
    finalize_integration_audit,
    get_audit_context,
    integration_error_response,
)
from app.api.v1.admin_attendance import router as admin_attendance_router
from app.api.v1.admin_auth import router as admin_auth_router
from app.api.v1.admin_employments import router as admin_employments_router
from app.api.v1.admin_export import router as admin_export_router
from app.api.v1.admin_instances import router as admin_instances_router
from app.api.v1.admin_integrations import router as admin_integrations_router
from app.api.v1.admin_settings import router as admin_settings_router
from app.api.v1.admin_shift_plan import router as admin_shift_plan_router
from app.api.v1.admin_smtp import router as admin_smtp_router
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.attendance import router as attendance_router
from app.api.v1.integration import router as integration_router
from app.api.v1.portal_auth import router as portal_auth_router
from app.api.v1.public_instances import router as public_instances_router
from app.brand.brand import APP_NAME_LONG
from app.config import Settings, get_settings
from app.db.schema_bootstrap import ensure_schema_up_to_date
from app.db.session import get_sessionmaker
from app.security.rate_limit import init_rate_limiting, limiter
from app.services.attendance_reminders import run_attendance_reminders_once
from app.services.prague_time import prague_time_payload


class _LimiterWithDefaults(Protocol):
    default_limits: list[str]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _deployed_backend_tag(settings: Settings) -> str:
    candidates = [
        Path("/opt/dagmar/backend/backend-version.json"),
        Path("/srv/hcasc/_repos/dagmar-backend/backend-version.json"),
    ]
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            tag = data.get("backend_commit")
            if isinstance(tag, str) and tag.strip():
                return tag.strip()
        except Exception:
            continue
    return settings.deploy_tag


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event: Event | None = None
        thread: Thread | None = None

        ensure_schema_up_to_date(settings)

        if not settings.database_url.startswith("sqlite"):
            stop_event = Event()

            def loop() -> None:
                session_factory = get_sessionmaker()
                while stop_event is not None and not stop_event.is_set():
                    run_attendance_reminders_once(settings, session_factory)
                    stop_event.wait(60)

            thread = Thread(target=loop, name="attendance-reminder-worker", daemon=True)
            thread.start()
            app.state.attendance_reminder_stop_event = stop_event
            app.state.attendance_reminder_thread = thread

        try:
            yield
        finally:
            if stop_event is not None:
                stop_event.set()
            if thread is not None:
                thread.join(timeout=2)

    app = FastAPI(
        title=APP_NAME_LONG,
        version="1.0.0",
        docs_url=None if settings.disable_docs else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.disable_docs else "/api/openapi.json",
        lifespan=lifespan,
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
        request.state.request_id = uuid.uuid4().hex
        response: JSONResponse | None = None
        is_integration = request.url.path.startswith(INTEGRATION_NAMESPACE)
        try:
            response = await call_next(request)
        except IntegrationError as exc:
            response = integration_error_response(request, exc.status_code, exc.code, exc.message)
        except HTTPException as exc:
            if is_integration:
                get_audit_context(request).error_code = "invalid_request" if exc.status_code == 400 else "internal_error"
                response = integration_error_response(
                    request,
                    exc.status_code,
                    "invalid_request" if exc.status_code == 400 else "internal_error",
                    str(exc.detail) if isinstance(exc.detail, str) else "Požadavek se nepodařilo zpracovat.",
                )
            else:
                raise exc
        except Exception:
            if is_integration:
                get_audit_context(request).error_code = "internal_error"
                response = integration_error_response(
                    request,
                    500,
                    "internal_error",
                    "Došlo k interní chybě.",
                )
            else:
                raise
        if response is None:
            raise RuntimeError("Middleware nevytvořila odpověď.")
        dur_ms = _now_ms() - start_ms
        response.headers["X-Request-Duration-Ms"] = str(dur_ms)
        response.headers["X-Request-ID"] = ensure_request_id(request)
        if is_integration:
            session = get_sessionmaker()()
            try:
                finalize_integration_audit(session, request, status_code=response.status_code)
            finally:
                session.close()
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            if request.url.path.startswith(INTEGRATION_NAMESPACE):
                get_audit_context(request).error_code = "invalid_request"
                return integration_error_response(request, 400, "invalid_request", "Neplatný požadavek.")
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

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(INTEGRATION_NAMESPACE):
            if exc.status_code == 404:
                get_audit_context(request).error_code = "not_found"
                return integration_error_response(request, 404, "not_found", "Požadovaný zdroj nebyl nalezen.")
            get_audit_context(request).error_code = "invalid_request" if exc.status_code == 400 else "internal_error"
            return integration_error_response(
                request,
                exc.status_code,
                "invalid_request" if exc.status_code == 400 else "internal_error",
                str(exc.detail) if isinstance(exc.detail, str) else "Požadavek se nepodařilo zpracovat.",
            )
        return await http_exception_handler(request, HTTPException(status_code=exc.status_code, detail=exc.detail))

    async def _health_payload() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/v1/health", include_in_schema=False)
    async def health_v1() -> dict[str, Any]:
        return await _health_payload()

    @app.get("/api/health", include_in_schema=False)
    async def health_compat() -> dict[str, Any]:
        return await _health_payload()

    @app.get("/api/version", include_in_schema=False)
    async def version() -> dict[str, Any]:
        return {
            "backend_deploy_tag": _deployed_backend_tag(settings),
            "environment": settings.environment,
        }

    @app.get("/api/v1/time", include_in_schema=False)
    async def time_v1() -> dict[str, Any]:
        return prague_time_payload()

    # Routers already carry full prefixes ("/api/v1/..."), so include without extra prefixes
    # to avoid duplicate paths like "/api/v1/api/v1/...".
    app.include_router(attendance_router)
    app.include_router(public_instances_router)

    app.include_router(admin_auth_router, tags=["admin"])
    app.include_router(admin_instances_router, tags=["admin"])
    app.include_router(admin_export_router, tags=["admin"])
    app.include_router(admin_attendance_router, tags=["admin"])
    app.include_router(admin_shift_plan_router, tags=["admin"])
    app.include_router(admin_settings_router, tags=["admin"])
    app.include_router(admin_users_router, tags=["admin"])
    app.include_router(admin_employments_router, tags=["admin"])
    app.include_router(admin_integrations_router, tags=["admin"])
    app.include_router(admin_smtp_router, tags=["admin"])
    app.include_router(portal_auth_router, tags=["portal"])
    app.include_router(integration_router, tags=["integration"])

    # Consistent JSON error for unhandled exceptions in API paths.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Do not leak details to client.
        if request.url.path.startswith("/api/"):
            if request.url.path.startswith(INTEGRATION_NAMESPACE):
                get_audit_context(request).error_code = "internal_error"
                return integration_error_response(request, 500, "internal_error", "Došlo k interní chybě.")
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "Internal server error"}},
            )
        raise exc

    return app


app = create_app()
