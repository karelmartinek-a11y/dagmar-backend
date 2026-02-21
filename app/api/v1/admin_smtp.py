# ruff: noqa: B008
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.config import Settings, get_settings
from app.db.models import AppSettings
from app.db.session import get_db
from app.security.crypto import encrypt_secret
from app.security.csrf import require_csrf

router = APIRouter(prefix="/api/v1/admin/smtp", tags=["admin-smtp"])


class SmtpOut(BaseModel):
    host: str | None = None
    port: int | None = None
    security: str | None = None
    username: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    password_set: bool = False


class SmtpIn(BaseModel):
    host: str | None = Field(default=None, max_length=255)
    port: int | None = None
    security: str | None = Field(default=None, max_length=16)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=512)
    from_email: str | None = Field(default=None, max_length=255)
    from_name: str | None = Field(default=None, max_length=255)


def _get_settings(db: Session) -> AppSettings:
    st = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if st is None:
        st = AppSettings(id=1, afternoon_cutoff_minutes=17 * 60)
        db.add(st)
        db.commit()
        db.refresh(st)
    return st


@router.get("", response_model=SmtpOut)
def get_smtp(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    st = _get_settings(db)
    return SmtpOut(
        host=st.smtp_host,
        port=st.smtp_port,
        security=st.smtp_security,
        username=st.smtp_username,
        from_email=st.smtp_from_email,
        from_name=st.smtp_from_name,
        password_set=bool(st.smtp_password),
    )


@router.put("", response_model=SmtpOut)
def set_smtp(
    payload: SmtpIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    st = _get_settings(db)
    st.smtp_host = payload.host.strip() if payload.host else None
    st.smtp_port = int(payload.port) if payload.port else None
    st.smtp_security = payload.security.strip().upper() if payload.security else None
    st.smtp_username = payload.username.strip() if payload.username else None
    st.smtp_from_email = payload.from_email.strip() if payload.from_email else None
    st.smtp_from_name = payload.from_name.strip() if payload.from_name else None
    if payload.password:
        smtp_secret = settings.smtp_password_secret or settings.session_secret
        st.smtp_password = encrypt_secret(payload.password, secret=smtp_secret)
    st.smtp_updated_at = datetime.now()
    db.add(st)
    db.commit()
    return SmtpOut(
        host=st.smtp_host,
        port=st.smtp_port,
        security=st.smtp_security,
        username=st.smtp_username,
        from_email=st.smtp_from_email,
        from_name=st.smtp_from_name,
        password_set=bool(st.smtp_password),
    )
