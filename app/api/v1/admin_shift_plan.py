from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.models import Base, Instance, InstanceStatus, ShiftPlan, ShiftPlanMonthInstance
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.utils.timeparse import parse_hhmm_or_none, parse_yyyy_mm_dd

router = APIRouter(tags=["admin"])


class ActiveInstanceOut(BaseModel):
    id: str
    display_name: Optional[str] = None
    employment_template: str


class ShiftPlanDayOut(BaseModel):
    date: str
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None


class ShiftPlanRowOut(BaseModel):
    instance_id: str
    display_name: Optional[str] = None
    employment_template: str
    days: list[ShiftPlanDayOut]


class ShiftPlanMonthOut(BaseModel):
    year: int
    month: int
    selected_instance_ids: list[str] = []
    active_instances: list[ActiveInstanceOut] = []
    rows: list[ShiftPlanRowOut] = []


class ShiftPlanSelectionIn(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    instance_ids: list[str] = Field(default_factory=list)


class ShiftPlanUpsertIn(BaseModel):
    instance_id: str = Field(..., min_length=1)
    date: str = Field(..., description="YYYY-MM-DD")
    arrival_time: Optional[str] = Field(None, description="HH:MM or null")
    departure_time: Optional[str] = Field(None, description="HH:MM or null")


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


@router.get("/api/v1/admin/shift-plan", response_model=ShiftPlanMonthOut)
def admin_get_shift_plan_month(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> ShiftPlanMonthOut:
    # Aktivní instance chceme vrátit i tehdy, když tabulky plánů chybí (aby UI mělo aspoň seznam).
    try:
        active_instances = db.execute(
            select(Instance)
            .where(Instance.status == InstanceStatus.ACTIVE)
            .order_by(Instance.display_name.asc(), Instance.created_at.asc())
        ).scalars().all()
    except SQLAlchemyError as e:
        logging.getLogger(__name__).warning("Instance query failed: %s", e)
        active_instances = []

    try:
        return _admin_get_shift_plan_month_impl(db=db, year=year, month=month, active_instances=active_instances)
    except SQLAlchemyError as e:
        logging.getLogger(__name__).warning("Shift plan tables unavailable: %s", e)
        active_out = [
            ActiveInstanceOut(id=i.id, display_name=i.display_name, employment_template=i.employment_template) for i in active_instances
        ]
        return ShiftPlanMonthOut(year=year, month=month, selected_instance_ids=[], active_instances=active_out, rows=[])


def _ensure_shift_plan_tables(db: Session) -> None:
    try:
        bind = db.get_bind()
        insp = inspect(bind)
        missing = []
        if not insp.has_table("shift_plan"):
            missing.append(ShiftPlan.__table__)
        if not insp.has_table("shift_plan_month_instances"):
            missing.append(ShiftPlanMonthInstance.__table__)
        if missing:
            Base.metadata.create_all(bind=bind, tables=missing)
    except Exception as e:
        logging.getLogger(__name__).warning("Unable to ensure shift plan tables: %s", e)


def _admin_get_shift_plan_month_impl(db: Session, *, year: int, month: int, active_instances=None) -> ShiftPlanMonthOut:
    _ensure_shift_plan_tables(db)
    start, end = _month_range(year, month)

    if active_instances is None:
        active_instances = db.execute(
            select(Instance)
            .where(Instance.status == InstanceStatus.ACTIVE)
            .order_by(Instance.display_name.asc(), Instance.created_at.asc())
        ).scalars().all()

    active_out = [
        ActiveInstanceOut(id=i.id, display_name=i.display_name, employment_template=i.employment_template) for i in active_instances
    ]

    selected = db.execute(
        select(ShiftPlanMonthInstance)
        .where(ShiftPlanMonthInstance.year == year)
        .where(ShiftPlanMonthInstance.month == month)
        .order_by(ShiftPlanMonthInstance.id.asc())
    ).scalars().all()
    selected_ids = [s.instance_id for s in selected]

    if not selected_ids:
        return ShiftPlanMonthOut(year=year, month=month, selected_instance_ids=[], active_instances=active_out, rows=[])

    insts = db.execute(select(Instance).where(Instance.id.in_(selected_ids))).scalars().all()
    inst_by_id = {i.id: i for i in insts}

    plan_rows = db.execute(
        select(ShiftPlan)
        .where(ShiftPlan.instance_id.in_(selected_ids))
        .where(ShiftPlan.date >= start)
        .where(ShiftPlan.date < end)
        .order_by(ShiftPlan.date.asc())
    ).scalars().all()
    plan_map: dict[tuple[str, dt.date], ShiftPlan] = {(p.instance_id, p.date): p for p in plan_rows}

    rows: list[ShiftPlanRowOut] = []
    for iid in selected_ids:
        inst = inst_by_id.get(iid)
        if not inst:
            continue
        cur = start
        days: list[ShiftPlanDayOut] = []
        while cur < end:
            p = plan_map.get((iid, cur))
            days.append(
                ShiftPlanDayOut(
                    date=cur.isoformat(),
                    arrival_time=p.arrival_time if p else None,
                    departure_time=p.departure_time if p else None,
                )
            )
            cur = cur + dt.timedelta(days=1)
        rows.append(
            ShiftPlanRowOut(
                instance_id=iid,
                display_name=inst.display_name,
                employment_template=inst.employment_template,
                days=days,
            )
        )

    return ShiftPlanMonthOut(year=year, month=month, selected_instance_ids=selected_ids, active_instances=active_out, rows=rows)


@router.put("/api/v1/admin/shift-plan", response_model=OkOut)
def admin_upsert_shift_plan(
    body: ShiftPlanUpsertIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    try:
        return _admin_upsert_shift_plan_impl(db=db, body=body)
    except SQLAlchemyError as e:
        logging.getLogger(__name__).warning("Shift plan write failed: %s", e)
        raise HTTPException(status_code=500, detail="Shift plan storage is not available") from e


def _admin_upsert_shift_plan_impl(db: Session, body: ShiftPlanUpsertIn) -> OkOut:
    _ensure_shift_plan_tables(db)
    inst = db.get(Instance, body.instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    try:
        day = parse_yyyy_mm_dd(body.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        arrival = parse_hhmm_or_none(body.arrival_time)
        departure = parse_hhmm_or_none(body.departure_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    existing = db.execute(
        select(ShiftPlan).where(
            ShiftPlan.instance_id == inst.id,
            ShiftPlan.date == day,
        )
    ).scalar_one_or_none()

    # Remove empty row to keep "existence" semantics simple.
    if arrival is None and departure is None:
        if existing is not None:
            db.delete(existing)
            db.commit()
        return OkOut(ok=True)

    if existing is None:
        existing = ShiftPlan(instance_id=inst.id, date=day, arrival_time=arrival, departure_time=departure)
        db.add(existing)
    else:
        existing.arrival_time = arrival
        existing.departure_time = departure

    db.commit()
    return OkOut(ok=True)


@router.put("/api/v1/admin/shift-plan/selection", response_model=OkOut)
def admin_set_shift_plan_selection(
    body: ShiftPlanSelectionIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OkOut:
    try:
        return _admin_set_shift_plan_selection_impl(db=db, body=body)
    except SQLAlchemyError as e:
        logging.getLogger(__name__).warning("Shift plan selection write failed: %s", e)
        raise HTTPException(status_code=500, detail="Shift plan storage is not available") from e


def _admin_set_shift_plan_selection_impl(db: Session, body: ShiftPlanSelectionIn) -> OkOut:
    _ensure_shift_plan_tables(db)
    # Keep order, remove duplicates & empties.
    uniq: list[str] = []
    seen: set[str] = set()
    for iid in body.instance_ids:
        if not iid or iid in seen:
            continue
        seen.add(iid)
        uniq.append(iid)

    if uniq:
        active_ids = set(
            db.execute(
                select(Instance.id)
                .where(Instance.status == InstanceStatus.ACTIVE)
                .where(Instance.id.in_(uniq))
            ).scalars().all()
        )
        missing = [iid for iid in uniq if iid not in active_ids]
        if missing:
            raise HTTPException(status_code=400, detail="Some instances are not ACTIVE or were not found")

    db.execute(
        delete(ShiftPlanMonthInstance).where(
            ShiftPlanMonthInstance.year == body.year,
            ShiftPlanMonthInstance.month == body.month,
        )
    )
    for iid in uniq:
        db.add(ShiftPlanMonthInstance(year=body.year, month=body.month, instance_id=iid))
    db.commit()
    return OkOut(ok=True)
