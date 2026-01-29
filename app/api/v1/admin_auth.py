# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import RedirectResponse

from app.config import Settings, get_settings
from app.security.csrf import csrf_issue_token
from app.security.passwords import verify_password
from app.security.rate_limit import limiter
from app.security.sessions import (
    clear_admin_session,
    get_admin_session,
    set_admin_session,
)

router = APIRouter(tags=["admin"])


class AdminLoginBody(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AdminMeResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class CsrfTokenResponse(BaseModel):
    csrf_token: str


@router.post("/api/v1/admin/login")
@limiter.limit("10/minute")
async def admin_login(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    """Admin login.

    Contract:
      - POST /api/v1/admin/login
      - body { username, password }
      - sets session cookie

    Notes:
      - Only a single admin credential pair is supported (seeded via env).
      - Session is server-side (in-memory) and intended for single-node deployment.
    """

    # Prevent timing attacks on username checks by always doing hash verify
    # when a hash is configured.
    configured_user = settings.admin_username
    configured_hash = settings.admin_password_hash

    if not configured_hash:
        raise HTTPException(
            status_code=503,
            detail="Admin účet není inicializován. Spusťte scripts/seed_admin.sh.",
        )

    payload: AdminLoginBody | None = None

    # Try JSON first; avoid framework-level 422 by parsing manually.
    try:
        raw_json = await request.json()
        payload = AdminLoginBody.model_validate(raw_json)
    except (ValidationError, Exception):
        payload = None

    if payload is None:
        try:
            form = await request.form()
            payload = AdminLoginBody(
                username=(form.get("username") or "").strip() or None,
                password=form.get("password") or "",
            )
        except ValidationError:
            raise HTTPException(status_code=400, detail="Vyplňte uživatelské jméno a heslo.") from None
        except Exception:
            raise HTTPException(status_code=400, detail="Nelze zpracovat přihlašovací údaje.") from None

    if not payload:
        raise HTTPException(status_code=400, detail="Vyplňte uživatelské jméno a heslo.")

    username = (payload.username or settings.admin_username or "").strip()
    if not username or not payload.password:
        raise HTTPException(status_code=400, detail="Vyplňte uživatelské jméno a heslo.")

    user_ok = username == configured_user
    pass_ok = verify_password(payload.password, configured_hash)

    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Neplatné přihlašovací údaje")

    set_admin_session(response=response, username=configured_user, settings=settings)

    # Issue CSRF token (returned in JSON; frontend stores in memory and sends header)
    csrf = csrf_issue_token(request=request, response=response, settings=settings)

    return {"ok": True, "csrf_token": csrf}


@router.get("/api/v1/admin/csrf", response_model=CsrfTokenResponse)
async def admin_csrf(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    csrf = csrf_issue_token(request=request, response=response, settings=settings)
    return {"csrf_token": csrf}


@router.post("/api/v1/admin/logout")
async def admin_logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    clear_admin_session(response=response, settings=settings)
    return {"ok": True}


@router.get("/api/v1/admin/logout", include_in_schema=False)
async def admin_logout_redirect(
    settings: Settings = Depends(get_settings),
):
    resp = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_admin_session(response=resp, settings=settings)
    return resp


@router.get("/api/v1/admin/me", response_model=AdminMeResponse)
async def admin_me(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    sess = get_admin_session(request=request, settings=settings)
    if not sess or not sess.is_authenticated:
        return AdminMeResponse(authenticated=False)
    return AdminMeResponse(authenticated=True, username=sess.username)
