# ruff: noqa: B008
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.v1.admin_users import EmploymentOut, _to_employment_out
from app.db.models import Attendance, AttendanceLock, AttendanceReminderEvent, Employment, PortalUser, ShiftPlan, ShiftPlanMonthInstance
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.services.employment_access import employment_label, employment_overlaps_month, employment_type_is_valid

router = APIRouter(tags=["admin-employments"])


class EmploymentCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    employment_type: str = Field(min_length=3, max_length=16)
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="YYYY-MM-DD nebo null")
    is_active: bool = True


class EmploymentUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    employment_type: str | None = Field(default=None, min_length=3, max_length=16)
    start_date: str | None = Field(default=None, description="YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="YYYY-MM-DD nebo null")
    is_active: bool | None = None
    confirm_delete_out_of_range: bool = False


class EmploymentDeleteOut(BaseModel):
    ok: bool = True
    deleted_attendance_count: int = 0
    deleted_shift_plan_count: int = 0
    deleted_attendance_lock_count: int = 0
    deleted_shift_plan_selection_count: int = 0
    deleted_reminder_count: int = 0


@dataclass
class RangeConflictSummary:
    attendance_count: int = 0
    shift_plan_count: int = 0
    attendance_lock_count: int = 0
    shift_plan_selection_count: int = 0
    reminder_count: int = 0
    min_date: date | None = None
    max_date: date | None = None

    def touch(self, candidate_min: date | None, candidate_max: date | None) -> None:
        if candidate_min is not None and (self.min_date is None or candidate_min < self.min_date):
            self.min_date = candidate_min
        if candidate_max is not None and (self.max_date is None or candidate_max > self.max_date):
            self.max_date = candidate_max


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Pole {field_name} musi byt ve formatu YYYY-MM-DD.") from exc


def _validate_period(start_date: date, end_date: date | None) -> None:
    if end_date is not None and end_date < start_date:
        raise HTTPException(status_code=400, detail="Datum ukonceni nesmi byt drive nez datum zacatku.")


def _is_date_out_of_range(day: date, start_date: date, end_date: date | None) -> bool:
    if day < start_date:
        return True
    if end_date is not None and day > end_date:
        return True
    return False


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _month_record_out_of_range(year: int, month: int, start_date: date, end_date: date | None) -> bool:
    month_start, month_end = _month_bounds(year, month)
    if month_end < start_date:
        return True
    if end_date is not None and month_start > end_date:
        return True
    return False


def _collect_range_conflicts(employment_id: int, start_date: date, end_date: date | None, db: Session) -> RangeConflictSummary:
    summary = RangeConflictSummary()

    attendance_rows = (
        db.execute(select(Attendance.date).where(Attendance.employment_id == employment_id).order_by(Attendance.date.asc()))
        .all()
    )
    offending_attendance = [row[0] for row in attendance_rows if _is_date_out_of_range(row[0], start_date, end_date)]
    if offending_attendance:
        summary.attendance_count = len(offending_attendance)
        summary.touch(offending_attendance[0], offending_attendance[-1])

    shift_rows = (
        db.execute(select(ShiftPlan.date).where(ShiftPlan.employment_id == employment_id).order_by(ShiftPlan.date.asc()))
        .all()
    )
    offending_shift = [row[0] for row in shift_rows if _is_date_out_of_range(row[0], start_date, end_date)]
    if offending_shift:
        summary.shift_plan_count = len(offending_shift)
        summary.touch(offending_shift[0], offending_shift[-1])

    lock_rows = (
        db.execute(
            select(AttendanceLock.year, AttendanceLock.month)
            .where(AttendanceLock.employment_id == employment_id)
            .order_by(AttendanceLock.year.asc(), AttendanceLock.month.asc())
        ).all()
    )
    offending_lock_months = [row for row in lock_rows if _month_record_out_of_range(row[0], row[1], start_date, end_date)]
    if offending_lock_months:
        summary.attendance_lock_count = len(offending_lock_months)
        first_lock = _month_bounds(offending_lock_months[0][0], offending_lock_months[0][1])
        last_lock = _month_bounds(offending_lock_months[-1][0], offending_lock_months[-1][1])
        summary.touch(first_lock[0], last_lock[1])

    selection_rows = (
        db.execute(
            select(ShiftPlanMonthInstance.year, ShiftPlanMonthInstance.month)
            .where(ShiftPlanMonthInstance.employment_id == employment_id)
            .order_by(ShiftPlanMonthInstance.year.asc(), ShiftPlanMonthInstance.month.asc())
        ).all()
    )
    offending_selection_months = [row for row in selection_rows if _month_record_out_of_range(row[0], row[1], start_date, end_date)]
    if offending_selection_months:
        summary.shift_plan_selection_count = len(offending_selection_months)
        first_selection = _month_bounds(offending_selection_months[0][0], offending_selection_months[0][1])
        last_selection = _month_bounds(offending_selection_months[-1][0], offending_selection_months[-1][1])
        summary.touch(first_selection[0], last_selection[1])

    reminder_rows = (
        db.execute(
            select(AttendanceReminderEvent.attendance_date)
            .where(AttendanceReminderEvent.employment_id == employment_id)
            .order_by(AttendanceReminderEvent.attendance_date.asc())
        ).all()
    )
    offending_reminders = [row[0] for row in reminder_rows if _is_date_out_of_range(row[0], start_date, end_date)]
    if offending_reminders:
        summary.reminder_count = len(offending_reminders)
        summary.touch(offending_reminders[0], offending_reminders[-1])

    return summary


def _raise_range_conflict(summary: RangeConflictSummary) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "employment_period_conflict",
            "message": "Mimo nove obdobi uvazku existuji navazana data. Zmenu je nutne potvrdit.",
            "attendance_count": summary.attendance_count,
            "shift_plan_count": summary.shift_plan_count,
            "attendance_lock_count": summary.attendance_lock_count,
            "shift_plan_selection_count": summary.shift_plan_selection_count,
            "reminder_count": summary.reminder_count,
            "problem_range_start": summary.min_date.isoformat() if summary.min_date is not None else None,
            "problem_range_end": summary.max_date.isoformat() if summary.max_date is not None else None,
            "requires_confirmation": True,
        },
    )


def _delete_out_of_range_records(employment_id: int, start_date: date, end_date: date | None, db: Session) -> EmploymentDeleteOut:
    attendance_deleted = db.execute(
        delete(Attendance).where(
            Attendance.employment_id == employment_id,
            or_(Attendance.date < start_date, Attendance.date > end_date if end_date is not None else False),
        )
    ).rowcount or 0
    shift_plan_deleted = db.execute(
        delete(ShiftPlan).where(
            ShiftPlan.employment_id == employment_id,
            or_(ShiftPlan.date < start_date, ShiftPlan.date > end_date if end_date is not None else False),
        )
    ).rowcount or 0

    lock_rows = (
        db.execute(select(AttendanceLock.id, AttendanceLock.year, AttendanceLock.month).where(AttendanceLock.employment_id == employment_id))
        .all()
    )
    lock_ids = [row[0] for row in lock_rows if _month_record_out_of_range(row[1], row[2], start_date, end_date)]
    attendance_lock_deleted = (
        db.execute(delete(AttendanceLock).where(AttendanceLock.id.in_(lock_ids))).rowcount or 0 if lock_ids else 0
    )

    selection_rows = (
        db.execute(
            select(ShiftPlanMonthInstance.id, ShiftPlanMonthInstance.year, ShiftPlanMonthInstance.month).where(
                ShiftPlanMonthInstance.employment_id == employment_id
            )
        ).all()
    )
    selection_ids = [row[0] for row in selection_rows if _month_record_out_of_range(row[1], row[2], start_date, end_date)]
    shift_plan_selection_deleted = (
        db.execute(delete(ShiftPlanMonthInstance).where(ShiftPlanMonthInstance.id.in_(selection_ids))).rowcount or 0
        if selection_ids
        else 0
    )

    reminder_deleted = db.execute(
        delete(AttendanceReminderEvent).where(
            AttendanceReminderEvent.employment_id == employment_id,
            or_(
                AttendanceReminderEvent.attendance_date < start_date,
                AttendanceReminderEvent.attendance_date > end_date if end_date is not None else False,
            ),
        )
    ).rowcount or 0

    return EmploymentDeleteOut(
        ok=True,
        deleted_attendance_count=attendance_deleted,
        deleted_shift_plan_count=shift_plan_deleted,
        deleted_attendance_lock_count=attendance_lock_deleted,
        deleted_shift_plan_selection_count=shift_plan_selection_deleted,
        deleted_reminder_count=reminder_deleted,
    )


@router.post("/api/v1/admin/users/{user_id}/employments", response_model=EmploymentOut)
def create_employment(
    user_id: int,
    payload: EmploymentCreateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    user = db.get(PortalUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen.")

    if not employment_type_is_valid(payload.employment_type):
        raise HTTPException(status_code=400, detail="Neplatny typ uvazku.")

    start_date = _parse_date(payload.start_date, "start_date")
    end_date = _parse_date(payload.end_date, "end_date")
    assert start_date is not None
    _validate_period(start_date, end_date)

    employment = Employment(
        user_id=user.id,
        title=payload.title.strip(),
        employment_type=payload.employment_type,
        start_date=start_date,
        end_date=end_date,
        is_active=payload.is_active,
    )
    db.add(employment)
    db.commit()
    db.refresh(employment)
    return _to_employment_out(employment)


@router.put("/api/v1/admin/employments/{employment_id}", response_model=EmploymentDeleteOut | EmploymentOut)
def update_employment(
    employment_id: int,
    payload: EmploymentUpdateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    employment = db.get(Employment, employment_id)
    if employment is None:
        raise HTTPException(status_code=404, detail="Uvazek nenalezen.")

    next_title = payload.title.strip() if payload.title is not None else employment.title
    next_type = payload.employment_type if payload.employment_type is not None else employment.employment_type
    if not employment_type_is_valid(next_type):
        raise HTTPException(status_code=400, detail="Neplatny typ uvazku.")

    next_start_date = _parse_date(payload.start_date, "start_date") if payload.start_date is not None else employment.start_date
    next_end_date = _parse_date(payload.end_date, "end_date") if payload.end_date is not None else employment.end_date
    assert next_start_date is not None
    _validate_period(next_start_date, next_end_date)

    summary = _collect_range_conflicts(employment.id, next_start_date, next_end_date, db)
    has_conflicts = any(
        (
            summary.attendance_count,
            summary.shift_plan_count,
            summary.attendance_lock_count,
            summary.shift_plan_selection_count,
            summary.reminder_count,
        )
    )
    if has_conflicts and not payload.confirm_delete_out_of_range:
        _raise_range_conflict(summary)

    delete_summary = None
    if has_conflicts and payload.confirm_delete_out_of_range:
        delete_summary = _delete_out_of_range_records(employment.id, next_start_date, next_end_date, db)

    employment.title = next_title
    employment.employment_type = next_type
    employment.start_date = next_start_date
    employment.end_date = next_end_date
    if payload.is_active is not None:
        employment.is_active = payload.is_active
    db.add(employment)
    db.commit()
    db.refresh(employment)

    if delete_summary is not None:
        return delete_summary
    return _to_employment_out(employment)


@router.delete("/api/v1/admin/employments/{employment_id}", response_model=EmploymentDeleteOut)
def delete_employment(
    employment_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    employment = db.get(Employment, employment_id)
    if employment is None:
        raise HTTPException(status_code=404, detail="Uvazek nenalezen.")

    related_count = (
        db.execute(select(func.count(Attendance.id)).where(Attendance.employment_id == employment_id)).scalar_one()
        + db.execute(select(func.count(ShiftPlan.id)).where(ShiftPlan.employment_id == employment_id)).scalar_one()
        + db.execute(select(func.count(AttendanceLock.id)).where(AttendanceLock.employment_id == employment_id)).scalar_one()
        + db.execute(select(func.count(ShiftPlanMonthInstance.id)).where(ShiftPlanMonthInstance.employment_id == employment_id)).scalar_one()
        + db.execute(select(func.count(AttendanceReminderEvent.id)).where(AttendanceReminderEvent.employment_id == employment_id)).scalar_one()
    )
    if related_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Uvazek obsahuje navazana data. Nastavte datum ukonceni nebo potvrzenou zmenu obdobi misto fyzickeho smazani.",
        )

    db.delete(employment)
    db.commit()
    return EmploymentDeleteOut(ok=True)
