# ruff: noqa: B008
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_admin
from app.db.models import Attendance, AttendanceLock, Employment, ShiftPlan
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.services.employment_access import employment_label
from app.utils.timeparse import parse_hhmm_or_none, parse_yyyy_mm_dd

router = APIRouter(tags=["admin"])


class AttendanceDayOut(BaseModel):
    date: str
    arrival_time: str | None = None
    departure_time: str | None = None
    planned_arrival_time: str | None = None
    planned_departure_time: str | None = None
    planned_status: str | None = None
    is_within_employment_period: bool


class AttendanceMonthOut(BaseModel):
    employment_id: int
    employment_label: str
    locked: bool = False
    days: list[AttendanceDayOut]


class AttendanceUpsertIn(BaseModel):
    employment_id: int = Field(..., ge=1)
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: str | None = Field(None, description="HH:MM or null")
    departure_time: str | None = Field(None, description="HH:MM or null")


class OkOut(BaseModel):
    ok: bool = True


class LockMonthIn(BaseModel):
    employment_id: int = Field(..., ge=1)
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


def _get_employment(employment_id: int, db: Session) -> Employment:
    employment = (
        db.execute(select(Employment).options(joinedload(Employment.user)).where(Employment.id == employment_id))
        .scalars()
        .first()
    )
    if employment is None:
        raise HTTPException(status_code=404, detail="Uvazek nenalezen.")
    return employment


@router.get("/api/v1/admin/attendance", response_model=AttendanceMonthOut)
def admin_get_month_attendance(
    employment_id: int = Query(..., ge=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> AttendanceMonthOut:
    employment = _get_employment(employment_id, db)
    start, end = _month_range(year, month)

    rows = db.execute(
        select(Attendance)
        .where(Attendance.employment_id == employment.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    ).scalars().all()
    by_date: dict[dt.date, Attendance] = {r.date: r for r in rows}

    plan_rows = db.execute(
        select(ShiftPlan)
        .where(ShiftPlan.employment_id == employment.id)
        .where(ShiftPlan.date >= start)
        .where(ShiftPlan.date < end)
    ).scalars().all()
    plan_by_date: dict[dt.date, ShiftPlan] = {r.date: r for r in plan_rows}

    days: list[AttendanceDayOut] = []
    cur = start
    while cur < end:
        row = by_date.get(cur)
        plan = plan_by_date.get(cur)
        days.append(
            AttendanceDayOut(
                date=cur.isoformat(),
                arrival_time=row.arrival_time if row else None,
                departure_time=row.departure_time if row else None,
                planned_arrival_time=plan.arrival_time if plan else None,
                planned_departure_time=plan.departure_time if plan else None,
                planned_status=plan.status if plan else None,
                is_within_employment_period=employment.start_date <= cur and (employment.end_date is None or cur <= employment.end_date),
            )
        )
        cur = cur + dt.timedelta(days=1)

    locked = (
        db.execute(
            select(AttendanceLock).where(
                AttendanceLock.employment_id == employment.id,
                AttendanceLock.year == year,
                AttendanceLock.month == month,
            )
        ).scalar_one_or_none()
        is not None
    )

    return AttendanceMonthOut(
        employment_id=employment.id,
        employment_label=employment_label(employment, employment.user.name if employment.user else None),
        days=days,
        locked=locked,
    )


@router.put("/api/v1/admin/attendance", response_model=OkOut)
def admin_upsert_attendance(
    body: AttendanceUpsertIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    employment = _get_employment(body.employment_id, db)

    try:
        day = parse_yyyy_mm_dd(body.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if day < employment.start_date or (employment.end_date is not None and day > employment.end_date):
        raise HTTPException(status_code=409, detail="Datum nelezi v obdobi platnosti vybraneho uvazku.")

    try:
        arrival = parse_hhmm_or_none(body.arrival_time)
        departure = parse_hhmm_or_none(body.departure_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = db.execute(
        select(Attendance).where(
            Attendance.employment_id == employment.id,
            Attendance.date == day,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Attendance(
            employment_id=employment.id,
            instance_id=employment.user.instance_id if employment.user else None,
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
    employment = _get_employment(body.employment_id, db)
    existing = db.execute(
        select(AttendanceLock).where(
            AttendanceLock.employment_id == employment.id,
            AttendanceLock.year == body.year,
            AttendanceLock.month == body.month,
        )
    ).scalar_one_or_none()
    if existing is None:
        lock = AttendanceLock(
            employment_id=employment.id,
            instance_id=employment.user.instance_id if employment.user else None,
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
    employment = _get_employment(body.employment_id, db)
    lock = db.execute(
        select(AttendanceLock).where(
            AttendanceLock.employment_id == employment.id,
            AttendanceLock.year == body.year,
            AttendanceLock.month == body.month,
        )
    ).scalar_one_or_none()
    if lock is None:
        return OkOut(ok=True)

    db.delete(lock)
    db.commit()
    return OkOut(ok=True)
