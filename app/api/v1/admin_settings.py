# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import AppSettings
from app.db.session import get_db
from app.security.csrf import require_csrf

router = APIRouter(prefix="/api/v1/admin/settings", tags=["admin-settings"])


def _hhmm_to_minutes(value: str) -> int:
    try:
        h, m = value.split(":")
        hh = int(h)
        mm = int(m)
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            raise ValueError()
        return hh * 60 + mm
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid time format, expected HH:MM.") from None


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


class SettingsOut(BaseModel):
    afternoon_cutoff: str


class SettingsIn(BaseModel):
    afternoon_cutoff: str = Field(min_length=5, max_length=5)


@router.get("", response_model=SettingsOut)
def get_settings(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    st = _get_settings(db)
    return SettingsOut(afternoon_cutoff=_minutes_to_hhmm(st.afternoon_cutoff_minutes))


@router.put("")
def set_settings(payload: SettingsIn, _admin=Depends(require_admin), _: None = Depends(require_csrf), db: Session = Depends(get_db)):
    st = _get_settings(db)
    st.afternoon_cutoff_minutes = _hhmm_to_minutes(payload.afternoon_cutoff)
    db.add(st)
    db.commit()
    return {"ok": True}
