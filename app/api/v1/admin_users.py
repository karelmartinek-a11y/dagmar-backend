# ruff: noqa: B008
from __future__ import annotations

import hashlib
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.config import Settings, get_settings
from app.db.models import (
    AppSettings,
    ClientType,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserResetToken,
    PortalUserRole,
)
from app.db.session import get_db
from app.security.crypto import decrypt_secret
from app.security.csrf import require_csrf

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])

RESET_TTL_HOURS = 24


class PortalUserOut(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None = None
    role: str
    has_password: bool
    profile_instance_id: str | None = None
    is_active: bool


class PortalUserListOut(BaseModel):
    users: list[PortalUserOut]


class PortalUserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=160)
    role: str = Field(min_length=1, max_length=32)


class PortalUserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    role: str | None = Field(default=None, min_length=1, max_length=32)
    profile_instance_id: str | None = Field(default=None, max_length=36)
    is_active: bool | None = None


class OkOut(BaseModel):
    ok: bool = True


def _get_settings(db: Session) -> AppSettings:
    st = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if st is None:
        st = AppSettings(id=1, afternoon_cutoff_minutes=17 * 60)
        db.add(st)
        db.commit()
        db.refresh(st)
    return st


def _send_reset_email(*, settings: Settings, cfg: AppSettings, to_email: str, reset_url: str) -> None:
    host = (cfg.smtp_host or "").strip()
    if not host or not cfg.smtp_port:
        raise ValueError("SMTP není nastaveno.")

    username = (cfg.smtp_username or "").strip()
    smtp_secret = settings.smtp_password_secret or settings.session_secret
    decrypted_password = decrypt_secret(cfg.smtp_password, secret=smtp_secret) if cfg.smtp_password else None
    password = decrypted_password.strip() if decrypted_password else None
    security = (cfg.smtp_security or "SSL").strip().upper()
    from_email = (cfg.smtp_from_email or username or "").strip()
    if not from_email:
        raise ValueError("Chybí odesílací e-mail.")

    msg = EmailMessage()
    msg["Subject"] = "Nastavení nebo změna hesla"
    msg["From"] = f"{cfg.smtp_from_name} <{from_email}>" if cfg.smtp_from_name else from_email
    msg["To"] = to_email
    msg.set_content(
        
            "Dobrý den,\n\n"
            "pro nastavení nebo změnu hesla použijte tento odkaz (platnost 24 hodin):\n\n"
            f"{reset_url}\n\n"
            "Pokud jste o změnu nežádali, ignorujte tento e-mail."
        
    )

    server: smtplib.SMTP
    if security == "SSL":
        server = smtplib.SMTP_SSL(host, int(cfg.smtp_port), timeout=20)
    else:
        server = smtplib.SMTP(host, int(cfg.smtp_port), timeout=20)
        if security == "STARTTLS":
            server.starttls()

    try:
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    finally:
        server.quit()


def _resolve_profile_instance_id(user: PortalUser) -> str | None:
    if not user.instance:
        return None
    return user.instance.profile_instance_id or user.instance_id


def _to_user_out(user: PortalUser) -> PortalUserOut:
    return PortalUserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role.value,
        has_password=bool(user.password_hash),
        profile_instance_id=_resolve_profile_instance_id(user),
        is_active=user.is_active,
    )


@router.get("", response_model=PortalUserListOut)
def list_users(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(PortalUser).order_by(PortalUser.name.asc())).scalars().all()
    out = [_to_user_out(u) for u in rows]
    return PortalUserListOut(users=out)


@router.post("", response_model=PortalUserOut)
def create_user(
    payload: PortalUserCreateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    if email == "provoz@hotelchodovasc.cz":
        raise HTTPException(status_code=400, detail="Tento e-mail je vyhrazen pro admin účet.")

    try:
        role_enum = PortalUserRole(payload.role)
    except Exception:
        raise HTTPException(status_code=400, detail="Neplatný druh pohledu.") from None

    exists = db.execute(select(PortalUser).where(PortalUser.email == email)).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Uživatel s tímto e‑mailem už existuje.")

    inst_id = None
    if role_enum == PortalUserRole.EMPLOYEE:
        now = datetime.now(UTC)
        inst_id = str(uuid4())
        inst = Instance(
            id=inst_id,
            client_type=ClientType.WEB,
            device_fingerprint=f"user:{inst_id}",
            status=InstanceStatus.ACTIVE,
            display_name=payload.name.strip(),
            employment_template="DPP_DPC",
            created_at=now,
            last_seen_at=now,
            activated_at=now,
        )
        db.add(inst)

    user = PortalUser(
        name=payload.name.strip(),
        email=email,
        role=role_enum,
        password_hash=None,
        instance_id=inst_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return _to_user_out(user)


@router.put("/{user_id}", response_model=PortalUserOut)
def update_user(
    user_id: int,
    payload: PortalUserUpdateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = db.get(PortalUser, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")

    if payload.name is not None:
        user.name = payload.name.strip()

    if payload.phone is not None:
        raw_phone = payload.phone.strip()
        user.phone = raw_phone or None

    if payload.email is not None:
        email = payload.email.strip().lower()
        if email == "provoz@hotelchodovasc.cz":
            raise HTTPException(status_code=400, detail="Tento e-mail je vyhrazen pro admin účet.")
        if email != user.email:
            exists = db.execute(
                select(PortalUser).where(PortalUser.email == email).where(PortalUser.id != user.id)
            ).scalars().first()
            if exists:
                raise HTTPException(status_code=409, detail="Uživatel s tímto e‑mailem už existuje.")
        user.email = email

    if payload.role is not None:
        try:
            user.role = PortalUserRole(payload.role)
        except Exception:
            raise HTTPException(status_code=400, detail="Neplatný druh pohledu.") from None

    if payload.profile_instance_id is not None:
        profile_instance_id = payload.profile_instance_id.strip() or None
        if profile_instance_id is not None:
            inst = db.get(Instance, profile_instance_id)
            if inst is None:
                raise HTTPException(status_code=400, detail="Profilová instance neexistuje.")
        user.instance_id = profile_instance_id

    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.add(user)
    db.commit()
    db.refresh(user)

    return _to_user_out(user)


@router.post("/{user_id}/send-reset", response_model=OkOut)
def send_reset_link(
    user_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = db.get(PortalUser, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(hours=RESET_TTL_HOURS)

    row = PortalUserResetToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    db.add(row)
    db.commit()

    cfg = _get_settings(db)
    reset_url = f"{settings.public_base_url}/reset?token={raw_token}"
    try:
        _send_reset_email(settings=settings, cfg=cfg, to_email=user.email, reset_url=reset_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Odeslání selhalo: {exc}") from exc

    return OkOut(ok=True)
