from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import Attendance, AttendanceLock, Instance
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.utils.timeparse import parse_hhmm_or_none, parse_yyyy_mm_dd

router = APIRouter(tags=["admin"])


class AttendanceDayOut(BaseModel):
    date: str
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None


class AttendanceMonthOut(BaseModel):
    days: list[AttendanceDayOut]
    locked: bool = False


class AttendanceUpsertIn(BaseModel):
    instance_id: str = Field(..., min_length=1)
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: Optional[str] = Field(None, description="HH:MM or null")
    departure_time: Optional[str] = Field(None, description="HH:MM or null")


class OkOut(BaseModel):
    ok: bool = True


class LockMonthIn(BaseModel):
    instance_id: str = Field(..., min_length=1)
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    if month < 1 or month > 12:
        raise ValueError("month out of range")
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start, end


@router.get("/api/v1/admin/attendance", response_model=AttendanceMonthOut)
def admin_get_month_attendance(
    instance_id: str = Query(..., description="Instance UUID"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> AttendanceMonthOut:
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    start, end = _month_range(year, month)

    rows = db.execute(
        select(Attendance)
        .where(Attendance.instance_id == inst.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    ).scalars().all()

    by_date: dict[dt.date, Attendance] = {r.date: r for r in rows}

    days: list[AttendanceDayOut] = []
    cur = start
    while cur < end:
        r = by_date.get(cur)
        days.append(
            AttendanceDayOut(
                date=cur.isoformat(),
                arrival_time=r.arrival_time if r else None,
                departure_time=r.departure_time if r else None,
            )
        )
        cur = cur + dt.timedelta(days=1)

    locked = (
        db.execute(
            select(AttendanceLock).where(
                AttendanceLock.instance_id == inst.id,
                AttendanceLock.year == year,
                AttendanceLock.month == month,
            )
        ).scalar_one_or_none()
        is not None
    )

    return AttendanceMonthOut(days=days, locked=locked)


@router.put("/api/v1/admin/attendance", response_model=OkOut)
def admin_upsert_attendance(
    body: AttendanceUpsertIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    inst = db.get(Instance, body.instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Validate date
    try:
        day = parse_yyyy_mm_dd(body.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Validate times (only format/range, no other business rules)
    try:
        arrival = parse_hhmm_or_none(body.arrival_time)
        departure = parse_hhmm_or_none(body.departure_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    existing = db.execute(
        select(Attendance).where(
            Attendance.instance_id == inst.id,
            Attendance.date == day,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Attendance(
            instance_id=inst.id,
            date=day,
            arrival_time=arrival,
            departure_time=departure,
        )
        db.add(existing)
    else:
        existing.arrival_time = arrival
        existing.departure_time = departure

    db.commit()
    return OkOut(ok=True)


@router.post("/api/v1/admin/attendance/lock", response_model=OkOut)
def lock_month(
    body: LockMonthIn,
    admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    inst = db.get(Instance, body.instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    existing = db.execute(
        select(AttendanceLock).where(
            AttendanceLock.instance_id == inst.id,
            AttendanceLock.year == body.year,
            AttendanceLock.month == body.month,
        )
    ).scalar_one_or_none()
    if existing is None:
        lock = AttendanceLock(
            instance_id=inst.id,
            year=body.year,
            month=body.month,
            locked_by=admin.username or None,
        )
        db.add(lock)
        db.commit()

    return OkOut(ok=True)


@router.post("/api/v1/admin/attendance/unlock", response_model=OkOut)
def unlock_month(
    body: LockMonthIn,
    _: None = Depends(require_csrf),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkOut:
    inst = db.get(Instance, body.instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    lock = db.execute(
        select(AttendanceLock).where(
            AttendanceLock.instance_id == inst.id,
            AttendanceLock.year == body.year,
            AttendanceLock.month == body.month,
        )
    ).scalar_one_or_none()
    if lock is None:
        return OkOut(ok=True)

    db.delete(lock)
    db.commit()
    return OkOut(ok=True)
