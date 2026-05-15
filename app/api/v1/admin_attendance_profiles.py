# ruff: noqa: B008
from __future__ import annotations

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import AttendanceProfile, ClientType, Instance, InstanceStatus, PortalUser
from app.db.session import get_db
from app.security.csrf import require_csrf

router = APIRouter(prefix="/api/v1/admin/attendance-profiles", tags=["admin-attendance-profiles"])


class AttendanceProfileOut(BaseModel):
    instance_id: str
    label: str
    valid_from: str | None = None
    valid_to: str | None = None
    assigned_users_count: int = 0


class AttendanceProfileListOut(BaseModel):
    profiles: list[AttendanceProfileOut]


class AttendanceProfileCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    valid_from: str | None = None
    valid_to: str | None = None


class AttendanceProfileUpdateIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    valid_from: str | None = None
    valid_to: str | None = None


def _normalize_label(raw: str) -> str:
    label = raw.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Nazev DL nesmi byt prazdny.")
    if not label.lower().startswith("dl "):
        label = f"DL {label}"
    return label


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Neplatne datum, ocekavan format YYYY-MM-DD.") from exc


def _to_out(db: Session, row: AttendanceProfile) -> AttendanceProfileOut:
    assigned_count = db.query(PortalUser).filter(PortalUser.instance_id == row.instance_id).count()
    return AttendanceProfileOut(
        instance_id=row.instance_id,
        label=row.label,
        valid_from=row.valid_from.isoformat() if row.valid_from else None,
        valid_to=row.valid_to.isoformat() if row.valid_to else None,
        assigned_users_count=assigned_count,
    )


@router.get("", response_model=AttendanceProfileListOut)
def list_attendance_profiles(_admin=Depends(require_admin), db: Session = Depends(get_db)) -> AttendanceProfileListOut:
    rows = db.execute(select(AttendanceProfile).order_by(AttendanceProfile.label.asc())).scalars().all()
    return AttendanceProfileListOut(profiles=[_to_out(db, row) for row in rows])


@router.post("", response_model=AttendanceProfileOut)
def create_attendance_profile(
    payload: AttendanceProfileCreateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AttendanceProfileOut:
    valid_from = _parse_date(payload.valid_from)
    valid_to = _parse_date(payload.valid_to)
    if valid_from and valid_to and valid_to < valid_from:
        raise HTTPException(status_code=400, detail="valid_to musi byt >= valid_from.")

    instance_id = str(uuid4())
    inst = Instance(
        id=instance_id,
        client_type=ClientType.WEB,
        device_fingerprint=f"dl:{instance_id}",
        status=InstanceStatus.ACTIVE,
        display_name=_normalize_label(payload.label),
    )
    row = AttendanceProfile(
        instance_id=instance_id,
        label=_normalize_label(payload.label),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(inst)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@router.put("/{instance_id}", response_model=AttendanceProfileOut)
def update_attendance_profile(
    instance_id: str,
    payload: AttendanceProfileUpdateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AttendanceProfileOut:
    row = db.execute(select(AttendanceProfile).where(AttendanceProfile.instance_id == instance_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Dochazkovy list nenalezen.")

    if payload.label is not None:
        label = _normalize_label(payload.label)
        row.label = label
        inst = db.get(Instance, instance_id)
        if inst is not None:
            inst.display_name = label
            db.add(inst)

    if payload.valid_from is not None:
        row.valid_from = _parse_date(payload.valid_from)
    if payload.valid_to is not None:
        row.valid_to = _parse_date(payload.valid_to)
    if row.valid_from and row.valid_to and row.valid_to < row.valid_from:
        raise HTTPException(status_code=400, detail="valid_to musi byt >= valid_from.")

    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@router.delete("/{instance_id}")
def delete_attendance_profile(
    instance_id: str,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    row = db.execute(select(AttendanceProfile).where(AttendanceProfile.instance_id == instance_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Dochazkovy list nenalezen.")

    linked_user = db.execute(select(PortalUser).where(PortalUser.instance_id == instance_id)).scalar_one_or_none()
    if linked_user is not None:
        raise HTTPException(status_code=409, detail="Dochazkovy list je prirazen uzivateli a nelze jej smazat.")

    inst = db.get(Instance, instance_id)
    db.delete(row)
    if inst is not None:
        db.delete(inst)
    db.commit()
    return {"ok": True}
