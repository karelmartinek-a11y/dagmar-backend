# ruff: noqa: B008
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import PortalUserAuth, require_portal_user_auth
from app.db.models import Attendance, Employment, ShiftPlan
from app.db.session import get_db
from app.services.employment_access import employment_is_valid_on_day, employment_label
from app.services.prague_time import prague_minutes_since_midnight, prague_today
from app.utils.timeparse import parse_hhmm_or_none

router = APIRouter(tags=["attendance"])


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
    days: list[AttendanceDayOut]


class AttendanceUpsertIn(BaseModel):
    employment_id: int
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: str | None = Field(None, description="HH:MM or null")
    departure_time: str | None = Field(None, description="HH:MM or null")


class OkOut(BaseModel):
    ok: bool = True


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    if month < 1 or month > 12:
        raise ValueError("month out of range")
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start, end


def _minutes_from_hhmm(value: str | None) -> int | None:
    if value is None:
        return None
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _require_accessible_employment(
    employment_id: int,
    auth: PortalUserAuth,
    db: Session,
) -> Employment:
    employment = db.get(Employment, employment_id)
    if employment is None or employment.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Uvazek nenalezen.")
    return employment


def _ensure_day_in_employment_period(employment: Employment, day: dt.date) -> None:
    if day < employment.start_date or (employment.end_date is not None and day > employment.end_date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zvolene datum nelezi v obdobi platnosti vybraneho uvazku.",
        )


def _enforce_user_forensic_rules(
    *,
    day: dt.date,
    arrival: str | None,
    departure: str | None,
    existing: Attendance | None,
) -> None:
    today = prague_today()
    if day > today:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Budouci pruchod uzivatel nesmi zadat.")

    if day == today:
        now_minutes = prague_minutes_since_midnight()
        if (arrival is not None and (_minutes_from_hhmm(arrival) or 0) > now_minutes) or (
            departure is not None and (_minutes_from_hhmm(departure) or 0) > now_minutes
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="U dnesniho dne nelze zadat cas v budoucnosti podle casu v Praze.",
            )
        return

    if existing is None:
        return

    if existing.arrival_time is not None and arrival != existing.arrival_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Na minulych dnech lze doplnit jen chybejici prichod nebo odchod. Ulozene hodnoty uz menit nejdou.",
        )
    if existing.departure_time is not None and departure != existing.departure_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Na minulych dnech lze doplnit jen chybejici prichod nebo odchod. Ulozene hodnoty uz menit nejdou.",
        )


@router.get("/api/v1/attendance", response_model=AttendanceMonthOut)
def get_month_attendance(
    employment_id: int = Query(..., ge=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    auth: PortalUserAuth = Depends(require_portal_user_auth),
) -> AttendanceMonthOut:
    start, end = _month_range(year, month)
    employment = _require_accessible_employment(employment_id, auth, db)

    rows = db.execute(
        select(Attendance)
        .where(Attendance.employment_id == employment.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    ).scalars().all()

    by_date: dict[dt.date, Attendance] = {r.date: r for r in rows}

    plan_by_date: dict[dt.date, ShiftPlan] = {}
    try:
        plan_rows = db.execute(
            select(ShiftPlan)
            .where(ShiftPlan.employment_id == employment.id)
            .where(ShiftPlan.date >= start)
            .where(ShiftPlan.date < end)
        ).scalars().all()
        plan_by_date = {r.date: r for r in plan_rows}
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).warning("ShiftPlan unavailable for attendance: %s", exc)

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

    return AttendanceMonthOut(
        employment_id=employment.id,
        employment_label=employment_label(employment, auth.user.name),
        days=days,
    )


@router.put("/api/v1/attendance", response_model=OkOut)
def upsert_attendance(
    body: AttendanceUpsertIn,
    db: Session = Depends(get_db),
    auth: PortalUserAuth = Depends(require_portal_user_auth),
) -> OkOut:
    try:
        day = dt.date.fromisoformat(body.date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format, expected YYYY-MM-DD",
        ) from exc

    employment = _require_accessible_employment(body.employment_id, auth, db)
    _ensure_day_in_employment_period(employment, day)

    try:
        arrival = parse_hhmm_or_none(body.arrival_time)
        departure = parse_hhmm_or_none(body.departure_time)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    existing = db.execute(
        select(Attendance).where(
            Attendance.employment_id == employment.id,
            Attendance.date == day,
        )
    ).scalar_one_or_none()

    _enforce_user_forensic_rules(day=day, arrival=arrival, departure=departure, existing=existing)

    if existing is None:
        existing = Attendance(
            employment_id=employment.id,
            instance_id=auth.instance.id,
            date=day,
            arrival_time=arrival,
            departure_time=departure,
        )
        db.add(existing)
    else:
        existing.arrival_time = arrival
        existing.departure_time = departure
        existing.instance_id = auth.instance.id

    db.commit()
    return OkOut(ok=True)
