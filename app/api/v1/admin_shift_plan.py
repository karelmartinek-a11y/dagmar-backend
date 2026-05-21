# ruff: noqa: B008
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_admin
from app.db.models import Employment, PortalUser, ShiftPlan, ShiftPlanMonthInstance
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.services.employment_access import employment_label, employment_overlaps_month
from app.utils.timeparse import parse_hhmm_or_none, parse_yyyy_mm_dd

router = APIRouter(tags=["admin"])


class ActiveEmploymentOut(BaseModel):
    id: int
    user_id: int
    user_name: str
    title: str
    employment_type: str
    display_label: str
    start_date: str
    end_date: str | None = None


class ShiftPlanDayOut(BaseModel):
    date: str
    arrival_time: str | None = None
    departure_time: str | None = None
    status: str | None = None
    is_within_employment_period: bool


class ShiftPlanRowOut(BaseModel):
    employment_id: int
    user_name: str
    title: str
    employment_type: str
    display_label: str
    days: list[ShiftPlanDayOut]


class ShiftPlanMonthOut(BaseModel):
    year: int
    month: int
    selected_employment_ids: list[int] = []
    available_employments: list[ActiveEmploymentOut] = []
    rows: list[ShiftPlanRowOut] = []


class ShiftPlanSelectionIn(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    employment_ids: list[int] = Field(default_factory=list)


class ShiftPlanUpsertIn(BaseModel):
    employment_id: int = Field(..., ge=1)
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: str | None = Field(None, description="HH:MM or null")
    departure_time: str | None = Field(None, description="HH:MM or null")
    status: str | None = Field(
        None, description="HOLIDAY | OFF | null", pattern="^(HOLIDAY|OFF)?$", examples=["HOLIDAY", "OFF"]
    )


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


def _to_active_employment_out(employment: Employment) -> ActiveEmploymentOut:
    user_name = employment.user.name if employment.user else f"Uživatel {employment.user_id}"
    return ActiveEmploymentOut(
        id=employment.id,
        user_id=employment.user_id,
        user_name=user_name,
        title=employment.title,
        employment_type=employment.employment_type,
        display_label=employment_label(employment, user_name),
        start_date=employment.start_date.isoformat(),
        end_date=employment.end_date.isoformat() if employment.end_date is not None else None,
    )


def _get_employment(employment_id: int, db: Session) -> Employment:
    employment = (
        db.execute(select(Employment).options(joinedload(Employment.user)).where(Employment.id == employment_id))
        .scalars()
        .first()
    )
    if employment is None:
        raise HTTPException(status_code=404, detail="Uvazek nenalezen.")
    return employment


def _load_available_employments(db: Session, year: int, month: int) -> list[Employment]:
    month_start, month_end = _month_range(year, month)
    rows = (
        db.execute(select(Employment).options(joinedload(Employment.user)).order_by(Employment.start_date.asc(), Employment.id.asc()))
        .scalars()
        .all()
    )
    return [row for row in rows if employment_overlaps_month(row, month_start, month_end)]


def _load_available_employment_rows(db: Session, year: int, month: int) -> list[SimpleNamespace]:
    month_start, month_end = _month_range(year, month)
    employments_table = Employment.__table__
    users_table = PortalUser.__table__

    rows = db.execute(
        select(
            employments_table.c.id,
            employments_table.c.user_id,
            employments_table.c.title,
            employments_table.c.employment_type,
            employments_table.c.start_date,
            employments_table.c.end_date,
            employments_table.c.is_active,
            users_table.c.name.label("user_name"),
        )
        .select_from(employments_table.outerjoin(users_table, users_table.c.id == employments_table.c.user_id))
        .order_by(employments_table.c.start_date.asc(), employments_table.c.id.asc())
    ).mappings().all()

    available: list[SimpleNamespace] = []
    for row in rows:
        employment = SimpleNamespace(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            title=row["title"],
            employment_type=row["employment_type"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            is_active=bool(row["is_active"]),
            user=SimpleNamespace(name=row["user_name"] or f"Uživatel {row['user_id']}"),
        )
        if employment_overlaps_month(cast(Employment, employment), month_start, month_end):
            available.append(employment)
    return available


@router.get("/api/v1/admin/shift-plan", response_model=ShiftPlanMonthOut)
def admin_get_shift_plan_month(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> ShiftPlanMonthOut:
    return _admin_get_shift_plan_month_impl(db=db, year=year, month=month)


def _admin_get_shift_plan_month_impl(db: Session, *, year: int, month: int) -> ShiftPlanMonthOut:
    start, end = _month_range(year, month)
    available_employments = _load_available_employment_rows(db, year, month)
    available_out = [_to_active_employment_out(cast(Employment, item)) for item in available_employments]
    available_ids = [item.id for item in available_employments]

    try:
        selected = db.execute(
            select(ShiftPlanMonthInstance)
            .where(ShiftPlanMonthInstance.year == year)
            .where(ShiftPlanMonthInstance.month == month)
            .order_by(ShiftPlanMonthInstance.id.asc())
        ).scalars().all()
        selected_ids = [row.employment_id for row in selected]
    except SQLAlchemyError:
        # Na starších produkčních datech může selhat pouze tabulka výběru měsíce.
        # Pro samotné zobrazení plánu je bezpečné spadnout zpět na všechny dostupné úvazky.
        selected_ids = []
    if not selected_ids:
        selected_ids = available_ids
    if not selected_ids:
        return ShiftPlanMonthOut(year=year, month=month, selected_employment_ids=[], available_employments=available_out, rows=[])

    employment_by_id = {item.id: item for item in available_employments if item.id in selected_ids}

    shift_plan_table = ShiftPlan.__table__
    plan_rows = db.execute(
        select(
            shift_plan_table.c.employment_id,
            shift_plan_table.c.date,
            shift_plan_table.c.arrival_time,
            shift_plan_table.c.departure_time,
            shift_plan_table.c.status,
        )
        .where(shift_plan_table.c.employment_id.in_(selected_ids))
        .where(shift_plan_table.c.date >= start)
        .where(shift_plan_table.c.date < end)
        .order_by(shift_plan_table.c.date.asc())
    ).mappings().all()
    plan_map: dict[tuple[int, dt.date], SimpleNamespace] = {
        (int(row["employment_id"]), row["date"]): SimpleNamespace(
            employment_id=int(row["employment_id"]),
            date=row["date"],
            arrival_time=row["arrival_time"],
            departure_time=row["departure_time"],
            status=row["status"],
        )
        for row in plan_rows
    }

    rows: list[ShiftPlanRowOut] = []
    for employment_id in selected_ids:
        employment = employment_by_id.get(employment_id)
        if employment is None:
            continue
        cur = start
        days: list[ShiftPlanDayOut] = []
        while cur < end:
            row = plan_map.get((employment_id, cur))
            days.append(
                ShiftPlanDayOut(
                    date=cur.isoformat(),
                    arrival_time=row.arrival_time if row else None,
                    departure_time=row.departure_time if row else None,
                    status=row.status if row else None,
                    is_within_employment_period=employment.start_date <= cur and (employment.end_date is None or cur <= employment.end_date),
                )
            )
            cur = cur + dt.timedelta(days=1)
        user_name = employment.user.name if employment.user else f"Uživatel {employment.user_id}"
        rows.append(
            ShiftPlanRowOut(
                employment_id=employment.id,
                user_name=user_name,
                title=employment.title,
                employment_type=employment.employment_type,
                display_label=employment_label(cast(Employment, employment), user_name),
                days=days,
            )
        )

    return ShiftPlanMonthOut(
        year=year,
        month=month,
        selected_employment_ids=selected_ids,
        available_employments=available_out,
        rows=rows,
    )


@router.put("/api/v1/admin/shift-plan", response_model=OkOut)
def admin_upsert_shift_plan(
    body: ShiftPlanUpsertIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    return _admin_upsert_shift_plan_impl(db=db, body=body)


def _admin_upsert_shift_plan_impl(db: Session, body: ShiftPlanUpsertIn) -> OkOut:
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
    if body.status not in (None, "HOLIDAY", "OFF"):
        raise HTTPException(status_code=400, detail="Invalid status, expected HOLIDAY or OFF or null")
    if body.status is not None:
        arrival = None
        departure = None

    existing = db.execute(
        select(ShiftPlan).where(
            ShiftPlan.employment_id == employment.id,
            ShiftPlan.date == day,
        )
    ).scalar_one_or_none()

    if arrival is None and departure is None and body.status is None:
        if existing is not None:
            db.delete(existing)
            db.commit()
        return OkOut(ok=True)

    if existing is None:
        existing = ShiftPlan(
            employment_id=employment.id,
            instance_id=employment.user.instance_id if employment.user else None,
            date=day,
            arrival_time=arrival,
            departure_time=departure,
            status=body.status,
        )
        db.add(existing)
    else:
        existing.arrival_time = arrival
        existing.departure_time = departure
        existing.status = body.status

    db.commit()
    return OkOut(ok=True)


@router.put("/api/v1/admin/shift-plan/selection", response_model=OkOut)
def admin_set_shift_plan_selection(
    body: ShiftPlanSelectionIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    return _admin_set_shift_plan_selection_impl(db=db, body=body)


def _admin_set_shift_plan_selection_impl(db: Session, body: ShiftPlanSelectionIn) -> OkOut:
    uniq: list[int] = []
    seen: set[int] = set()
    for employment_id in body.employment_ids:
        if employment_id in seen:
            continue
        employment = _get_employment(employment_id, db)
        month_start, month_end = _month_range(body.year, body.month)
        if not employment_overlaps_month(employment, month_start, month_end):
            raise HTTPException(status_code=400, detail="Nektery uvazek nelezi ve zvolenem mesici.")
        seen.add(employment_id)
        uniq.append(employment_id)

    db.execute(
        delete(ShiftPlanMonthInstance).where(
            ShiftPlanMonthInstance.year == body.year,
            ShiftPlanMonthInstance.month == body.month,
        )
    )
    for employment_id in uniq:
        db.add(ShiftPlanMonthInstance(year=body.year, month=body.month, employment_id=employment_id))
    db.commit()
    return OkOut(ok=True)
