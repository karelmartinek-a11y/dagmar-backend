# ruff: noqa: B008
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    AppSettings,
    Employment,
    PortalUser,
    PortalUserResetToken,
    PortalUserRole,
)
from app.db.session import get_db
from app.security.lockout import clear_user_lockout
from app.security.passwords import hash_password, verify_password_details
from app.security.tokens import issue_instance_token_once, rotate_instance_token
from app.services.employment_access import (
    employment_is_valid_on_day,
    employment_label,
    select_login_employments,
)
from app.services.prague_time import prague_today

router = APIRouter(prefix="/api/v1/portal", tags=["portal-auth"])


class PortalLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=1, max_length=256)


class LoginEmploymentOut(BaseModel):
    id: int
    title: str
    employment_type: str
    start_date: str
    end_date: str | None = None
    is_active: bool
    is_current: bool
    label: str


class PortalLoginOut(BaseModel):
    instance_token: str
    display_name: str
    employment_id: int | None = None
    available_employments: list[LoginEmploymentOut]
    afternoon_cutoff: str | None = None


class PortalResetIn(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=8, max_length=512)


class OkOut(BaseModel):
    ok: bool = True


def _minutes_to_hhmm(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _get_settings(db: Session) -> AppSettings:
    st = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if st is None:
        st = AppSettings(id=1, afternoon_cutoff_minutes=17 * 60)
        db.add(st)
        db.commit()
        db.refresh(st)
    return st


def _record_login_failure(*, detail: str) -> NoReturn:
    raise HTTPException(status_code=401, detail=detail)


def _to_login_employment_out(employment: Employment, today) -> LoginEmploymentOut:
    return LoginEmploymentOut(
        id=employment.id,
        title=employment.title,
        employment_type=employment.employment_type,
        start_date=employment.start_date.isoformat(),
        end_date=employment.end_date.isoformat() if employment.end_date is not None else None,
        is_active=employment.is_active,
        is_current=employment_is_valid_on_day(employment, today),
        label=employment_label(employment),
    )


@router.post("/login", response_model=PortalLoginOut)
def portal_login(payload: PortalLoginIn, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    clear_user_lockout(db, actor_type="portal", principal=email)
    db.commit()

    user = (
        db.execute(
            select(PortalUser)
            .options(selectinload(PortalUser.employments))
            .where(PortalUser.email == email)
        )
        .scalars()
        .first()
    )
    if user is None:
        _record_login_failure(detail="Neplatne prihlasovaci udaje")
    if not user.is_active or user.password_hash is None:
        _record_login_failure(detail="Neplatne prihlasovaci udaje")
    if user.role != PortalUserRole.EMPLOYEE:
        _record_login_failure(detail="Nepodporovany typ uctu")

    password_verification = verify_password_details(payload.password, user.password_hash)
    if not password_verification.valid:
        _record_login_failure(detail="Neplatne prihlasovaci udaje")
    if password_verification.needs_rehash:
        user.password_hash = hash_password(payload.password).value
        db.add(user)

    if not user.instance_id or user.instance is None:
        raise HTTPException(status_code=409, detail="Uzivatel nema pripraveny pristupovy token")

    today = prague_today()
    selection = select_login_employments(user, today)
    if not selection.available:
        raise HTTPException(
            status_code=403,
            detail="Prihlaseni neni povoleno, protoze uzivatel nema dostupny uvazek v povolenem prihlasovacim okne.",
        )

    token = issue_instance_token_once(db, user.instance)
    if token is None:
        token = rotate_instance_token(db, user.instance)
    user.instance.last_seen_at = datetime.now(UTC)
    db.add(user.instance)
    clear_user_lockout(db, actor_type="portal", principal=email)

    st = _get_settings(db)
    db.commit()

    return PortalLoginOut(
        instance_token=token,
        display_name=user.name,
        employment_id=selection.default.id if selection.default is not None else None,
        available_employments=[_to_login_employment_out(item, today) for item in selection.available],
        afternoon_cutoff=_minutes_to_hhmm(st.afternoon_cutoff_minutes),
    )


@router.post("/reset", response_model=OkOut)
def portal_reset(payload: PortalResetIn, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    row = db.execute(
        select(PortalUserResetToken)
        .where(PortalUserResetToken.token_hash == token_hash)
        .where(PortalUserResetToken.used_at.is_(None))
        .where(PortalUserResetToken.expires_at > now)
    ).scalars().first()

    if not row or not row.user:
        raise HTTPException(status_code=400, detail="Odkaz je neplatny nebo vyprsel")

    try:
        new_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row.user.password_hash = new_hash.value
    row.used_at = now
    db.add(row.user)
    db.add(row)
    clear_user_lockout(db, actor_type="portal", principal=row.user.email.lower())
    db.commit()

    return OkOut(ok=True)
