# ruff: noqa: B008
from __future__ import annotations

import hashlib
import secrets
import smtplib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_admin
from app.config import Settings, get_settings
from app.db.models import (
    AppSettings,
    AuthLockoutState,
    ClientType,
    Employment,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserResetToken,
    PortalUserRole,
)
from app.db.session import get_db
from app.security.crypto import decrypt_secret
from app.security.csrf import require_csrf
from app.security.lockout import as_utc, clear_user_lockout, is_locked, revoke_unlock_tokens
from app.security.passwords import hash_password
from app.services.employment_access import employment_label, select_login_employments
from app.services.prague_time import prague_today

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])

RESET_TTL_HOURS = 24


class EmploymentOut(BaseModel):
    id: int
    user_id: int
    title: str
    employment_type: str
    start_date: str
    end_date: str | None = None
    is_active: bool
    label: str


class PortalUserOut(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None = None
    role: str
    has_password: bool
    is_active: bool
    is_locked: bool = False
    locked_until: str | None = None
    login_status: str
    login_status_reason: str | None = None
    employments: list[EmploymentOut]


class PortalUserListOut(BaseModel):
    users: list[PortalUserOut]


class PortalUserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    role: str = Field(min_length=1, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    is_active: bool = True


class PortalUserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    role: str | None = Field(default=None, min_length=1, max_length=32)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class PortalUserPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=256)


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
        raise ValueError("SMTP neni nastaveno.")

    username = (cfg.smtp_username or "").strip()
    smtp_secret = settings.smtp_password_secret or settings.session_secret
    decrypted_password = decrypt_secret(cfg.smtp_password, secret=smtp_secret) if cfg.smtp_password else None
    password = decrypted_password.strip() if decrypted_password else None
    security = (cfg.smtp_security or "SSL").strip().upper()
    from_email = (cfg.smtp_from_email or username or "").strip()
    if not from_email:
        raise ValueError("Chybi odesilaci e-mail.")

    msg = EmailMessage()
    msg["Subject"] = "Nastaveni nebo zmena hesla"
    msg["From"] = f"{cfg.smtp_from_name} <{from_email}>" if cfg.smtp_from_name else from_email
    msg["To"] = to_email
    msg.set_content(
        "Dobry den,\n\n"
        "pro nastaveni nebo zmenu hesla pouzijte tento odkaz (platnost 24 hodin):\n\n"
        f"{reset_url}\n\n"
        "Pokud jste o zmenu nezadali, ignorujte tento e-mail."
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


def _normalize_phone(raw_phone: str | None) -> str | None:
    if raw_phone is None:
        return None
    phone = raw_phone.strip()
    return phone or None


def _safe_iso_date(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _employment_sort_key(employment: Employment) -> tuple[date, int]:
    start_date = employment.start_date if isinstance(employment.start_date, date) else date.max
    return (start_date, employment.id)


def _to_employment_out(employment: Employment) -> EmploymentOut:
    return EmploymentOut(
        id=employment.id,
        user_id=employment.user_id,
        title=(employment.title or "").strip() or "Bez názvu úvazku",
        employment_type=str(employment.employment_type or "").strip() or "DPP_DPC",
        start_date=_safe_iso_date(employment.start_date) or "1970-01-01",
        end_date=_safe_iso_date(employment.end_date),
        is_active=employment.is_active,
        label=employment_label(employment, user_name=getattr(employment.user, "name", None)),
    )


def _user_login_status(user: PortalUser) -> tuple[str, str | None]:
    if not user.is_active:
        return "DEACTIVATED", "Ucet je rucne deaktivovany administratorem."
    today = prague_today()
    try:
        selection = select_login_employments(user, today)
    except Exception:
        return "EMPLOYMENT_WINDOW_BLOCKED", "Uzivatel ma nekonzistentni historicka data uvazku."
    if selection.available:
        return "ACTIVE", None
    if user.employments:
        return "EMPLOYMENT_WINDOW_BLOCKED", "Zadny uvazek neni v povolenem prihlasovacim okne."
    return "EMPLOYMENT_WINDOW_BLOCKED", "Uzivatel nema zadny uvazek."


def _to_user_out(user: PortalUser, lock_state: AuthLockoutState | None = None) -> PortalUserOut:
    locked_until = as_utc(lock_state.locked_until) if lock_state is not None else None
    login_status, login_status_reason = _user_login_status(user)
    employments = sorted(user.employments, key=_employment_sort_key)
    return PortalUserOut(
        id=user.id,
        name=(user.name or "").strip(),
        email=(user.email or "").strip(),
        phone=user.phone,
        role=user.role.value if hasattr(user.role, "value") else str(user.role or ""),
        has_password=bool(user.password_hash),
        is_active=user.is_active,
        is_locked=is_locked(lock_state),
        locked_until=locked_until.isoformat() if locked_until is not None else None,
        login_status=login_status,
        login_status_reason=login_status_reason,
        employments=[_to_employment_out(item) for item in employments],
    )


def _invalidate_instance_token(user: PortalUser, db: Session) -> None:
    inst = user.instance or (db.get(Instance, user.instance_id) if user.instance_id else None)
    if inst is None:
        return
    inst.token_hash = None
    inst.token_issued_at = None
    db.add(inst)


def _apply_password(db: Session, user: PortalUser, raw_password: str | None) -> None:
    if raw_password is None:
        return
    password = raw_password.strip()
    if not password:
        raise HTTPException(status_code=400, detail="Heslo nesmi byt prazdne.")
    try:
        new_hash = hash_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user.password_hash = new_hash.value
    db.execute(delete(PortalUserResetToken).where(PortalUserResetToken.user_id == user.id))
    _invalidate_instance_token(user, db)
    clear_user_lockout(db, actor_type="portal", principal=user.email.lower())
    revoke_unlock_tokens(db, actor_type="portal", principal=user.email.lower())


@router.get("", response_model=PortalUserListOut)
def list_users(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.execute(select(PortalUser).options(selectinload(PortalUser.employments)).order_by(PortalUser.name.asc()))
        .scalars()
        .all()
    )
    principals = [user.email.lower() for user in rows if user.email]
    lock_rows = (
        db.execute(
            select(AuthLockoutState).where(
                AuthLockoutState.actor_type == "portal",
                AuthLockoutState.principal.in_(principals),
            )
        ).scalars().all()
        if principals
        else []
    )
    locks_by_principal = {row.principal: row for row in lock_rows}
    out = [_to_user_out(u, locks_by_principal.get(u.email.lower())) for u in rows]
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
        raise HTTPException(status_code=400, detail="Tento e-mail je vyhrazen pro admin ucet.")

    try:
        role_enum = PortalUserRole(payload.role)
    except Exception:
        raise HTTPException(status_code=400, detail="Neplatna role uzivatele.") from None

    exists = db.execute(select(PortalUser).where(PortalUser.email == email)).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Uzivatel s timto e-mailem uz existuje.")

    now = datetime.now(UTC)
    inst = Instance(
        id=str(uuid4()),
        client_type=ClientType.WEB,
        device_fingerprint=f"user:{email}",
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
        phone=_normalize_phone(payload.phone),
        role=role_enum,
        password_hash=None,
        is_active=payload.is_active,
        instance_id=inst.id,
    )
    db.add(user)
    db.flush()

    if payload.password is not None:
        _apply_password(db, user, payload.password)

    db.commit()
    db.refresh(user)
    db.refresh(inst)
    user = (
        db.execute(select(PortalUser).options(selectinload(PortalUser.employments)).where(PortalUser.id == user.id))
        .scalars()
        .one()
    )
    return _to_user_out(user)


@router.put("/{user_id}", response_model=PortalUserOut)
def update_user(
    user_id: int,
    payload: PortalUserUpdateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = (
        db.execute(select(PortalUser).options(selectinload(PortalUser.employments)).where(PortalUser.id == int(user_id)))
        .scalars()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")

    if payload.name is not None:
        user.name = payload.name.strip()
        if user.instance is not None:
            user.instance.display_name = user.name
            db.add(user.instance)

    if payload.phone is not None:
        user.phone = _normalize_phone(payload.phone)

    if payload.email is not None:
        email = payload.email.strip().lower()
        if email == "provoz@hotelchodovasc.cz":
            raise HTTPException(status_code=400, detail="Tento e-mail je vyhrazen pro admin ucet.")
        if email != user.email:
            exists = db.execute(
                select(PortalUser).where(PortalUser.email == email).where(PortalUser.id != user.id)
            ).scalars().first()
            if exists:
                raise HTTPException(status_code=409, detail="Uzivatel s timto e-mailem uz existuje.")
            clear_user_lockout(db, actor_type="portal", principal=user.email.lower())
            revoke_unlock_tokens(db, actor_type="portal", principal=user.email.lower())
            user.email = email

    if payload.role is not None:
        try:
            user.role = PortalUserRole(payload.role)
        except Exception:
            raise HTTPException(status_code=400, detail="Neplatna role uzivatele.") from None

    if payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            _invalidate_instance_token(user, db)

    if payload.password is not None:
        _apply_password(db, user, payload.password)

    db.add(user)
    db.commit()
    db.refresh(user)
    user = (
        db.execute(select(PortalUser).options(selectinload(PortalUser.employments)).where(PortalUser.id == user.id))
        .scalars()
        .one()
    )
    return _to_user_out(user)


@router.post("/{user_id}/set-password", response_model=PortalUserOut)
def set_user_password(
    user_id: int,
    payload: PortalUserPasswordIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = (
        db.execute(select(PortalUser).options(selectinload(PortalUser.employments)).where(PortalUser.id == int(user_id)))
        .scalars()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")
    _apply_password(db, user, payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_user_out(user)


@router.delete("/{user_id}", response_model=OkOut)
def delete_user(
    user_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = db.get(PortalUser, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")

    instance_id = user.instance_id
    clear_user_lockout(db, actor_type="portal", principal=user.email.lower())
    revoke_unlock_tokens(db, actor_type="portal", principal=user.email.lower())
    db.delete(user)

    if instance_id:
        other_links = db.execute(
            select(PortalUser).where(PortalUser.instance_id == instance_id).where(PortalUser.id != user.id)
        ).scalars().first()
        if other_links is None:
            inst = db.get(Instance, instance_id)
            if inst is not None:
                db.delete(inst)

    db.commit()
    return OkOut(ok=True)


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
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")

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
        raise HTTPException(status_code=400, detail=f"Odeslani selhalo: {exc}") from exc

    return OkOut(ok=True)


@router.post("/{user_id}/unlock", response_model=OkOut)
def unlock_user(
    user_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = db.get(PortalUser, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")

    clear_user_lockout(db, actor_type="portal", principal=user.email.lower())
    revoke_unlock_tokens(db, actor_type="portal", principal=user.email.lower())
    db.commit()
    return OkOut(ok=True)


@router.get("/{user_id}/employments", response_model=list[EmploymentOut])
def list_user_employments(user_id: int, _admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.execute(select(Employment).where(Employment.user_id == user_id).order_by(Employment.start_date.asc(), Employment.id.asc()))
        .scalars()
        .all()
    )
    return [_to_employment_out(row) for row in rows]
