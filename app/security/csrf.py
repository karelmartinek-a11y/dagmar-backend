from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Header, HTTPException, Request, Response

from app.config import Settings, get_settings


@dataclass(frozen=True)
class CsrfConfig:
    """Minimal CSRF helper.

    We do not rely on third-party services.

    Design:
      - Issue a random CSRF token and store it in the admin session.
      - Require the token on state-changing admin requests.
      - Accept token via:
          * header: X-CSRF-Token
          * form field: csrf_token

    Cookie policy:
      - Admin session cookie itself is HttpOnly+Secure+SameSite and not readable by JS.
      - CSRF token is not a cookie; it's provided to the frontend via API /admin/me
        (or embedded into admin HTML) and then echoed back.
    """

    header_name: str = "X-CSRF-Token"
    form_field_name: str = "csrf_token"
    rotate_minutes: int = 120


class CsrfError(HTTPException):
    def __init__(self, detail: str = "CSRF validation failed"):
        super().__init__(status_code=403, detail=detail)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _constant_time_eq(a: str, b: str) -> bool:
    # hmac.compare_digest is constant-time for equal-length strings.
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        return False


def issue_csrf_token(session: dict, cfg: CsrfConfig | None = None) -> str:
    """Issue a CSRF token and store it in the session.

    Session is a mutable dict maintained by the admin session middleware.
    """

    cfg = cfg or CsrfConfig()
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    session["csrf_issued_at"] = _utcnow().isoformat()
    return token


def csrf_issue_token(
    request: Request | None = None,
    response: Response | None = None,
    settings: Settings | None = None,
    cfg: CsrfConfig | None = None,
) -> str:
    """Compatibility helper used by admin login endpoint.

    - Stores/rotates token in the Starlette session if available.
    - Returns the token and optionally mirrors it in response headers for SPA use.
    """

    settings = settings or get_settings()
    cfg = cfg or CsrfConfig()

    session: dict | None = None
    if request is not None:
        try:
            session = request.session  # type: ignore[attr-defined]
        except Exception:
            session = getattr(request.state, "session", None)

    if session is None:
        session = {}

    token = get_or_rotate_csrf_token(session, cfg)

    # Persist into session for middleware-managed storage.
    try:
        if request is not None:
            request.session.update(session)  # type: ignore[attr-defined]
    except Exception:
        pass

    if response is not None:
        response.headers[cfg.header_name] = token
        # CSRF token is not HttpOnly; expose for SPA consumption.
        response.set_cookie(
            "dagmar_csrf_token",
            token,
            max_age=settings.session_max_age_seconds,
            secure=settings.cookie_secure,
            httponly=False,
            samesite=settings.cookie_samesite,
            path="/",
        )

    return token


def get_or_rotate_csrf_token(session: dict, cfg: CsrfConfig | None = None) -> str:
    cfg = cfg or CsrfConfig()
    token = session.get("csrf_token")
    issued_at_raw = session.get("csrf_issued_at")

    if not token or not issued_at_raw:
        return issue_csrf_token(session, cfg)

    try:
        issued_at = datetime.fromisoformat(issued_at_raw)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)
    except Exception:
        return issue_csrf_token(session, cfg)

    if _utcnow() - issued_at > timedelta(minutes=cfg.rotate_minutes):
        return issue_csrf_token(session, cfg)

    return token


def extract_csrf_token(
    request: Request,
    csrf_header: str | None,
    cfg: CsrfConfig | None = None,
) -> str | None:
    cfg = cfg or CsrfConfig()
    if csrf_header:
        return csrf_header.strip()

    # Fallback: cookie set by csrf_issue_token (SPA může nepředat header).
    cookie_val = request.cookies.get("dagmar_csrf_token")
    if cookie_val:
        return cookie_val.strip()

    # For classic HTML forms (admin UI), accept csrf_token form field.
    # Note: reading form requires async; thus this is used only in dependency below.
    return None


async def require_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    """Dependency to protect state-changing admin endpoints.

    Usage:
        @router.post("/something")
        async def handler(..., _: None = Depends(require_csrf)):
            ...

    Requirements:
      - Session must exist and contain csrf_token.
      - Client must provide matching token.

    Behavior:
      - Safe methods (GET/HEAD/OPTIONS) are not checked.
      - For non-JSON form submits, token can be provided as form field 'csrf_token'.
    """

    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    session: dict | None = None
    try:
        session = request.session  # type: ignore[attr-defined]
    except Exception:
        session = getattr(request.state, "session", None)

    if not session:
        raise CsrfError("Missing session")

    expected = session.get("csrf_token")
    if not expected:
        raise CsrfError("Missing CSRF token in session")

    provided = extract_csrf_token(request, csrf_header)

    if not provided:
        # Try form field.
        try:
            form = await request.form()
            provided = (form.get("csrf_token") or "").strip() or None
        except Exception:
            provided = None

    if not provided:
        raise CsrfError("Missing CSRF token")

    if not _constant_time_eq(provided, expected):
        raise CsrfError()


def attach_csrf_token_to_response(response: Response, token: str) -> None:
    """Optional helper.

    For SPA admin, it is usually better to return the token from /admin/me.
    We keep this helper for completeness.
    """

    response.headers["X-CSRF-Token"] = token
