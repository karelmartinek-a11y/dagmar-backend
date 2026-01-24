from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings, Settings

"""Admin session implementation for DAGMAR.

Design goals:
- Host-only, no third-party services.
- Cookie-based session id (opaque), server-side session data stored in PostgreSQL.
- HttpOnly + Secure + SameSite cookie flags.
- Deterministic and small; intended for FastAPI.

DB model expectation (see app/db/models.py):
- AdminSession(id, session_id_hash, created_at, expires_at, data_json)

The cookie contains a random session_id, not the DB primary key.
We store only a hash of session_id in DB (to avoid leakage if DB read).

NOTE: This file intentionally contains no FastAPI route handlers.
"""


SESSION_COOKIE_NAME = "dagmar_admin_session"
# Legacy alias expected by some endpoints.
ADMIN_SESSION_COOKIE = SESSION_COOKIE_NAME


@dataclass(frozen=True)
class AdminSession:
    username: Optional[str]
    issued_at: int

    @property
    def is_authenticated(self) -> bool:
        return bool(self.username)


@dataclass(frozen=True)
class SessionCookieConfig:
    name: str = SESSION_COOKIE_NAME
    path: str = "/"
    # Secure cookie is required in production. For local dev you can disable via env.
    secure: bool = True
    httponly: bool = True
    samesite: str = "lax"  # "lax" is safe for typical admin UX; POSTs are CSRF-protected.
    max_age_seconds: int = 60 * 60 * 12  # 12 hours


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_session_id() -> str:
    # 32 bytes => 256-bit session id
    return _b64url(os.urandom(32))


def session_id_hash(session_id: str) -> str:
    # Use SHA-256 over raw session id. We additionally namespace the hash.
    digest = hashlib.sha256(("dagmar:" + session_id).encode("utf-8")).hexdigest()
    return digest


def _sign(payload: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(mac)


def _encode_cookie_value(session_id: str, secret: str) -> str:
    """Signed cookie value.

    Format: v1.{session_id}.{sig}
    Where sig = HMAC-SHA256("v1." + session_id)

    This prevents trivial tampering; session validity is still server-side.
    """

    prefix = "v1."
    payload = prefix + session_id
    sig = _sign(payload, secret)
    return f"{payload}.{sig}"


def _decode_cookie_value(cookie_value: str, secret: str) -> Optional[str]:
    """Return session_id if signature matches, otherwise None."""

    try:
        parts = cookie_value.split(".")
        if len(parts) != 3:
            return None
        v, session_id, sig = parts
        if v != "v1":
            return None
        payload = "v1." + session_id
        expected = _sign(payload, secret)
        if not hmac.compare_digest(expected, sig):
            return None
        if not session_id or len(session_id) < 20:
            # sanity check
            return None
        return session_id
    except Exception:
        return None


def set_admin_session_cookie(resp: Response, *, session_id: str, cookie_cfg: SessionCookieConfig, secret: str) -> None:
    value = _encode_cookie_value(session_id, secret)
    resp.set_cookie(
        cookie_cfg.name,
        value,
        max_age=cookie_cfg.max_age_seconds,
        path=cookie_cfg.path,
        secure=cookie_cfg.secure,
        httponly=cookie_cfg.httponly,
        samesite=cookie_cfg.samesite,
    )


def clear_admin_session_cookie(resp: Response, *, cookie_cfg: SessionCookieConfig) -> None:
    resp.delete_cookie(cookie_cfg.name, path=cookie_cfg.path)


@dataclass
class AdminSessionData:
    admin_username: str
    issued_at: int

    def to_json(self) -> str:
        return json.dumps({"admin_username": self.admin_username, "issued_at": self.issued_at}, separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "AdminSessionData":
        obj = json.loads(s)
        return AdminSessionData(admin_username=str(obj["admin_username"]), issued_at=int(obj["issued_at"]))


def get_session_id_from_request(req: Request, *, cookie_cfg: SessionCookieConfig, secret: str) -> Optional[str]:
    raw = req.cookies.get(cookie_cfg.name)
    if not raw:
        return None
    return _decode_cookie_value(raw, secret)


# ---- DB helpers (SQLAlchemy) -------------------------------------------------


def create_admin_session_row(
    db: Any,
    *,
    session_id: str,
    data: AdminSessionData,
    ttl_seconds: int,
    AdminSessionModel: Any,
) -> Any:
    """Create a server-side session row.

    Parameters:
    - db: SQLAlchemy Session
    - session_id: opaque session id
    - data: session data
    - ttl_seconds: expiry window
    - AdminSessionModel: ORM model class

    Returns created ORM object.
    """

    now = int(time.time())
    expires_at = now + int(ttl_seconds)

    row = AdminSessionModel(
        session_id_hash=session_id_hash(session_id),
        created_at=now,
        expires_at=expires_at,
        data_json=data.to_json(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_admin_session_row(db: Any, *, session_id: str, AdminSessionModel: Any) -> None:
    h = session_id_hash(session_id)
    db.query(AdminSessionModel).filter(AdminSessionModel.session_id_hash == h).delete()
    db.commit()


def load_admin_session_data(db: Any, *, session_id: str, AdminSessionModel: Any) -> Optional[AdminSessionData]:
    """Load and validate server-side session.

    Returns None if not found or expired.
    """

    h = session_id_hash(session_id)
    row = db.query(AdminSessionModel).filter(AdminSessionModel.session_id_hash == h).one_or_none()
    if row is None:
        return None

    now = int(time.time())
    if int(row.expires_at) < now:
        # Expired - best-effort cleanup
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        return None

    try:
        return AdminSessionData.from_json(str(row.data_json))
    except Exception:
        # Corrupted session: invalidate.
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        return None


def prune_expired_sessions(db: Any, *, AdminSessionModel: Any, limit: int = 500) -> int:
    """Delete expired sessions.

    Intended to be called occasionally (e.g., on startup or via admin login).
    """

    now = int(time.time())
    q = db.query(AdminSessionModel).filter(AdminSessionModel.expires_at < now).limit(int(limit))
    rows = q.all()
    if not rows:
        return 0
    n = len(rows)
    for r in rows:
        db.delete(r)
    db.commit()
    return n


# ---- Minimal cookie-based admin session helpers (no DB storage) ------------------------

def _cookie_cfg_from_settings(settings: Settings) -> SessionCookieConfig:
    return SessionCookieConfig(
        name=settings.admin_session_cookie,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age_seconds=settings.session_max_age_seconds,
    )


def set_admin_session(
    response: Response,
    *,
    username: str,
    settings: Settings | None = None,
) -> None:
    """Issue signed admin session cookie."""

    settings = settings or get_settings()
    issued_at = int(time.time())
    payload = json.dumps({"u": username, "iat": issued_at}, separators=(",", ":"))
    sig = _sign(payload, settings.session_secret)
    token = f"{_b64url(payload.encode('utf-8'))}.{sig}"

    cfg = _cookie_cfg_from_settings(settings)
    response.set_cookie(
        cfg.name,
        token,
        max_age=cfg.max_age_seconds,
        path=cfg.path,
        secure=cfg.secure,
        httponly=True,
        samesite=cfg.samesite,
    )


def clear_admin_session(
    response: Response,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    cfg = _cookie_cfg_from_settings(settings)
    response.delete_cookie(cfg.name, path=cfg.path)


def get_admin_session(
    request: Request,
    settings: Settings | None = None,
) -> AdminSession:
    """Validate admin session cookie and return auth state."""

    settings = settings or get_settings()
    cfg = _cookie_cfg_from_settings(settings)
    raw = request.cookies.get(cfg.name)
    if not raw:
        return AdminSession(username=None, issued_at=int(time.time()))

    try:
        payload_b64, sig = raw.split(".", 1)
    except ValueError:
        return AdminSession(username=None, issued_at=int(time.time()))

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        payload = payload_bytes.decode("utf-8")
    except Exception:
        return AdminSession(username=None, issued_at=int(time.time()))

    expected_sig = _sign(payload, settings.session_secret)
    if not hmac.compare_digest(expected_sig, sig):
        return AdminSession(username=None, issued_at=int(time.time()))

    try:
        data = json.loads(payload)
        username = str(data.get("u") or "")
        issued_at = int(data.get("iat") or 0)
    except Exception:
        return AdminSession(username=None, issued_at=int(time.time()))

    if not username:
        return AdminSession(username=None, issued_at=issued_at)

    # Expiry check
    if int(time.time()) - issued_at > cfg.max_age_seconds:
        return AdminSession(username=None, issued_at=issued_at)

    return AdminSession(username=username, issued_at=issued_at)
