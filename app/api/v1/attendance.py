from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_instance
from app.db.models import Attendance, AttendanceLock, Base, Instance, ShiftPlan
from app.db.session import get_db
from app.utils.timeparse import parse_hhmm_or_none

router = APIRouter(tags=["attendance"])


class AttendanceDayOut(BaseModel):
    date: str
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    planned_arrival_time: Optional[str] = None
    planned_departure_time: Optional[str] = None


class AttendanceMonthOut(BaseModel):
    days: list[AttendanceDayOut]


class AttendanceUpsertIn(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: Optional[str] = Field(None, description='HH:MM or null')
    departure_time: Optional[str] = Field(None, description='HH:MM or null')


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
    _ensure_shift_plan_tables(db)
    start, end = _month_range(year, month)

    if _is_locked(db, inst.id, year, month):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"code": "ATTENDANCE_MONTH_LOCKED", "message": "Docházka pro tento měsíc je uzavřená administrátorem."},
        )

    rows = db.execute(
        select(Attendance)
        .where(Attendance.instance_id == inst.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    ).scalars().all()

    by_date: dict[dt.date, Attendance] = {r.date: r for r in rows}

    plan_by_date: dict[dt.date, ShiftPlan] = {}
    try:
        plan_rows = db.execute(
            select(ShiftPlan)
            .where(ShiftPlan.instance_id == inst.id)
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

    return AttendanceMonthOut(days=days)


def _ensure_shift_plan_tables(db: Session) -> None:
    try:
        bind = db.get_bind()
        insp = inspect(bind)
        missing = []
        if not insp.has_table("shift_plan"):
            missing.append(ShiftPlan.__table__)
        if not insp.has_table("shift_plan_month_instances"):
            missing.append(Base.metadata.tables.get("shift_plan_month_instances"))
        if missing:
            Base.metadata.create_all(bind=bind, tables=[t for t in missing if t is not None])
    except Exception as e:
        logging.getLogger(__name__).warning("Unable to ensure shift plan tables (attendance): %s", e)


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
        raise ValueError("Invalid date format, expected YYYY-MM-DD") from e
    if _is_locked(db, inst.id, day.year, day.month):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"code": "ATTENDANCE_MONTH_LOCKED", "message": "Docházka pro tento měsíc je uzavřená administrátorem."},
        )

    # Validate date
    # day already parsed above

    # Validate times (only format/range, no other business rules)
    arrival = parse_hhmm_or_none(body.arrival_time)
    departure = parse_hhmm_or_none(body.departure_time)

    # Upsert
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
