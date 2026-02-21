# ruff: noqa: B008
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_instance, resolve_profile_instance
from app.db.models import Attendance, AttendanceLock, Instance, ShiftPlan
from app.db.session import get_db
from app.utils.timeparse import parse_hhmm_or_none

router = APIRouter(tags=["attendance"])


class AttendanceDayOut(BaseModel):
    date: str
    arrival_time: str | None = None
    departure_time: str | None = None
    planned_arrival_time: str | None = None
    planned_departure_time: str | None = None


class AttendanceMonthOut(BaseModel):
    days: list[AttendanceDayOut]
    instance_display_name: str


class AttendanceUpsertIn(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: str | None = Field(None, description='HH:MM or null')
    departure_time: str | None = Field(None, description='HH:MM or null')


class OkOut(BaseModel):
    ok: bool = True


def _is_locked(db: Session, instance_id: str, year: int, month: int) -> bool:
    lock = db.execute(
        select(AttendanceLock).where(
            AttendanceLock.instance_id == instance_id,
            AttendanceLock.year == year,
            AttendanceLock.month == month,
        )
    ).scalar_one_or_none()
    return lock is not None


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    if month < 1 or month > 12:
        raise ValueError("month out of range")
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start, end


@router.get("/api/v1/attendance", response_model=AttendanceMonthOut)
def get_month_attendance(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    inst: Instance = Depends(require_instance),
) -> AttendanceMonthOut:
    start, end = _month_range(year, month)
    profile = resolve_profile_instance(db, inst)

    if _is_locked(db, profile.id, year, month):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"code": "ATTENDANCE_MONTH_LOCKED", "message": "Docházka pro tento měsíc je uzavřená administrátorem."},
        )

    rows = db.execute(
        select(Attendance)
        .where(Attendance.instance_id == profile.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    ).scalars().all()

    by_date: dict[dt.date, Attendance] = {r.date: r for r in rows}

    plan_by_date: dict[dt.date, ShiftPlan] = {}
    try:
        plan_rows = db.execute(
            select(ShiftPlan)
            .where(ShiftPlan.instance_id == profile.id)
            .where(ShiftPlan.date >= start)
            .where(ShiftPlan.date < end)
        ).scalars().all()
        plan_by_date = {r.date: r for r in plan_rows}
    except SQLAlchemyError as e:
        logging.getLogger(__name__).warning("ShiftPlan unavailable for attendance: %s", e)

    days: list[AttendanceDayOut] = []
    cur = start
    while cur < end:
        r = by_date.get(cur)
        p = plan_by_date.get(cur)
        days.append(
            AttendanceDayOut(
                date=cur.isoformat(),
                arrival_time=r.arrival_time if r else None,
                departure_time=r.departure_time if r else None,
                planned_arrival_time=p.arrival_time if p else None,
                planned_departure_time=p.departure_time if p else None,
            )
        )
        cur = cur + dt.timedelta(days=1)

    display_name = profile.display_name or f"Zařízení {profile.id[:8]}"

    return AttendanceMonthOut(days=days, instance_display_name=display_name)


@router.put("/api/v1/attendance", response_model=OkOut)
def upsert_attendance(
    body: AttendanceUpsertIn,
    db: Session = Depends(get_db),
    inst: Instance = Depends(require_instance),
) -> OkOut:
    # Prevent writes to locked months
    try:
        day = dt.date.fromisoformat(body.date)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format, expected YYYY-MM-DD",
        ) from e
    profile = resolve_profile_instance(db, inst)
    if _is_locked(db, profile.id, day.year, day.month):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"code": "ATTENDANCE_MONTH_LOCKED", "message": "Docházka pro tento měsíc je uzavřená administrátorem."},
        )

    # Validate date
    # day already parsed above

    # Validate times (only format/range, no other business rules)
    try:
        arrival = parse_hhmm_or_none(body.arrival_time)
        departure = parse_hhmm_or_none(body.departure_time)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Upsert
    existing = db.execute(
        select(Attendance).where(
            Attendance.instance_id == profile.id,
            Attendance.date == day,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Attendance(
            instance_id=profile.id,
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
