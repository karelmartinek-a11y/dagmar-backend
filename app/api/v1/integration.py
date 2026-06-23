# ruff: noqa: B008
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import IntegrationAuth, require_integration_auth
from app.api.integration_common import (
    decode_cursor,
    encode_cursor,
    get_audit_context,
    parse_iso_date,
    raise_integration_error,
    set_attendance_write_audit,
    utc_isoformat,
)
from app.config import Settings, get_settings
from app.db import models
from app.db.session import get_db
from app.security.integration_rate_limit import rate_limit_dependency
from app.services.employment_access import employment_label
from app.services.integration_admin import (
    DATA_SCOPE_ACTIVE_ONLY,
    DATA_SCOPE_SELECTED_EMPLOYEES,
    DATA_SCOPE_SELECTED_EMPLOYMENTS,
    infer_data_scope_mode,
)
from app.utils.timeparse import parse_hhmm_or_none

router = APIRouter(prefix="/api/v1/integration", tags=["integration"])

TIMEZONE = "Europe/Prague"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_PERIOD_DAYS = 31


class PaginationOut(BaseModel):
    limit: int
    next_cursor: str | None
    has_more: bool


class ListResponse(BaseModel):
    data: list[dict[str, Any]]
    pagination: PaginationOut


class IntegrationAttendanceWriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arrival_time: str | None = Field(default=None, description="HH:MM nebo null")
    departure_time: str | None = Field(default=None, description="HH:MM nebo null")


class IntegrationAttendanceCreateIn(IntegrationAttendanceWriteIn):
    employment_id: int = Field(..., ge=1)
    date: str = Field(..., description="YYYY-MM-DD")


class IntegrationAttendancePatchIn(IntegrationAttendanceWriteIn):
    expected_updated_at: str | None = Field(default=None, description="UTC timestamp ve formátu ISO 8601")


class IntegrationAttendanceDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: str | None = Field(default=None, description="UTC timestamp ve formátu ISO 8601")


class IntegrationAttendanceMutationOut(BaseModel):
    attendance_id: int
    employment_id: int
    employee_id: int | None = None
    date: str
    arrival_time: str | None = None
    departure_time: str | None = None
    last_changed_at: str | None = None
    deleted: bool = False


def _normalize_limit(limit: int) -> int:
    if limit < 1:
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "invalid_request", "Limit musí být kladné číslo.")
    return min(limit, MAX_LIMIT)


def _ensure_scope(auth: IntegrationAuth, scope: str) -> None:
    if scope not in set(auth.client.scopes or []):
        raise_integration_error(status.HTTP_403_FORBIDDEN, "insufficient_scope", "Klient nemá oprávnění pro tento endpoint.")


def _allowed_employment_ids(auth: IntegrationAuth) -> set[int] | None:
    if infer_data_scope_mode(auth.client) != DATA_SCOPE_SELECTED_EMPLOYMENTS:
        return None
    values = {int(item) for item in (auth.client.allowed_employment_ids or [])}
    return values if values else None


def _allowed_employee_ids(auth: IntegrationAuth) -> set[int] | None:
    if infer_data_scope_mode(auth.client) != DATA_SCOPE_SELECTED_EMPLOYEES:
        return None
    values = {int(item) for item in (auth.client.allowed_employee_ids or [])}
    return values if values else None


def _check_requested_scope(
    *,
    auth: IntegrationAuth,
    db: Session,
    employment_id: int | None,
    employee_id: int | None,
) -> None:
    scope_mode = infer_data_scope_mode(auth.client)
    allowed_employment_ids = _allowed_employment_ids(auth)
    allowed_employee_ids = _allowed_employee_ids(auth)
    if employment_id is not None and allowed_employment_ids is not None and employment_id not in allowed_employment_ids:
        raise_integration_error(status.HTTP_403_FORBIDDEN, "insufficient_scope", "Požadovaný úvazek není v rozsahu klienta.")
    if employee_id is not None and allowed_employee_ids is not None and employee_id not in allowed_employee_ids:
        raise_integration_error(status.HTTP_403_FORBIDDEN, "insufficient_scope", "Požadovaný zaměstnanec není v rozsahu klienta.")
    if scope_mode == DATA_SCOPE_ACTIVE_ONLY and employment_id is not None:
        employment = db.get(models.Employment, employment_id)
        if employment is not None and not employment.is_active:
            raise_integration_error(status.HTTP_403_FORBIDDEN, "insufficient_scope", "Požadovaný úvazek není v rozsahu klienta.")
    if scope_mode == DATA_SCOPE_ACTIVE_ONLY and employee_id is not None:
        active_employment_count = int(
            db.execute(
                select(func.count(models.Employment.id)).where(
                    models.Employment.user_id == employee_id,
                    models.Employment.is_active.is_(True),
                )
            ).scalar_one()
        )
        if active_employment_count == 0:
            raise_integration_error(status.HTTP_403_FORBIDDEN, "insufficient_scope", "Požadovaný zaměstnanec není v rozsahu klienta.")


def _filter_employments_by_scope(
    employments: Sequence[models.Employment],
    auth: IntegrationAuth,
) -> list[models.Employment]:
    scope_mode = infer_data_scope_mode(auth.client)
    allowed_employment_ids = _allowed_employment_ids(auth)
    allowed_employee_ids = _allowed_employee_ids(auth)
    filtered: list[models.Employment] = []
    for item in employments:
        if scope_mode == DATA_SCOPE_ACTIVE_ONLY and not item.is_active:
            continue
        if allowed_employment_ids is not None and item.id not in allowed_employment_ids:
            continue
        if allowed_employee_ids is not None and item.user_id not in allowed_employee_ids:
            continue
        if (
            scope_mode == DATA_SCOPE_SELECTED_EMPLOYEES
            and not bool(getattr(auth.client, "include_inactive_employments", False))
            and not item.is_active
        ):
            continue
        filtered.append(item)
    return filtered


def _paginate_records(
    *,
    request: Request,
    records: list[dict[str, Any]],
    limit: int,
    cursor_key: str,
) -> dict[str, Any]:
    has_more = len(records) > limit
    page = records[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = encode_cursor({cursor_key: page[-1][cursor_key]})
    get_audit_context(request).row_count = len(page)
    return {
        "data": page,
        "pagination": {
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    }


def _period(date_from: str, date_to: str) -> tuple[date, date]:
    start = parse_iso_date(date_from, field_name="date_from")
    end = parse_iso_date(date_to, field_name="date_to")
    if end < start:
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "invalid_request", "Pole date_to nesmí být dříve než date_from.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "period_too_large", "Požadované období je příliš velké.")
    return start, end


def _employment_payload(employment: models.Employment) -> dict[str, Any]:
    display_name = employment.user.name if employment.user is not None else None
    return {
        "employment_id": employment.id,
        "employee_id": employment.user_id,
        "display_label": employment_label(employment, display_name),
        "title": employment.title,
        "employment_type": employment.employment_type,
        "start_date": employment.start_date.isoformat(),
        "end_date": employment.end_date.isoformat() if employment.end_date is not None else None,
        "is_active": employment.is_active,
        "last_changed_at": utc_isoformat(employment.updated_at),
        "cursor_key": employment.id,
    }


def _load_lock_map(
    db: Session,
    *,
    employment_ids: list[int],
    start: date,
    end: date,
) -> dict[tuple[int, int, int], models.AttendanceLock]:
    if not employment_ids:
        return {}
    rows = db.execute(
        select(models.AttendanceLock)
        .where(models.AttendanceLock.employment_id.in_(employment_ids))
        .where((models.AttendanceLock.year * 100 + models.AttendanceLock.month) >= (start.year * 100 + start.month))
        .where((models.AttendanceLock.year * 100 + models.AttendanceLock.month) <= (end.year * 100 + end.month))
    ).scalars().all()
    return {(row.employment_id, row.year, row.month): row for row in rows}


def _parse_expected_updated_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise_integration_error(
            status.HTTP_400_BAD_REQUEST,
            "validation_error",
            "Pole expected_updated_at musí být platné datum a čas v ISO 8601.",
        )
    if parsed.tzinfo is None:
        raise_integration_error(
            status.HTTP_400_BAD_REQUEST,
            "validation_error",
            "Pole expected_updated_at musí obsahovat časové pásmo.",
        )
    return parsed.astimezone(UTC)


def _parse_attendance_times(*, arrival_time: str | None, departure_time: str | None) -> tuple[str | None, str | None]:
    try:
        return (parse_hhmm_or_none(arrival_time), parse_hhmm_or_none(departure_time))
    except ValueError as exc:
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "validation_error", str(exc))
        raise AssertionError from exc


def _attendance_state(row: models.Attendance | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "arrival_time": row.arrival_time,
        "departure_time": row.departure_time,
        "last_changed_at": utc_isoformat(row.updated_at),
    }


def _serialize_attendance_mutation(row: models.Attendance, *, deleted: bool = False) -> dict[str, Any]:
    employee_id = row.employment.user_id if row.employment is not None else None
    return {
        "attendance_id": row.id,
        "employment_id": row.employment_id,
        "employee_id": employee_id,
        "date": row.date.isoformat(),
        "arrival_time": row.arrival_time,
        "departure_time": row.departure_time,
        "last_changed_at": utc_isoformat(row.updated_at),
        "deleted": deleted,
    }


def _get_employment_for_write(db: Session, *, employment_id: int) -> models.Employment:
    employment = (
        db.execute(
            select(models.Employment)
            .options(joinedload(models.Employment.user))
            .where(models.Employment.id == employment_id)
        )
        .scalars()
        .first()
    )
    if employment is None:
        raise_integration_error(status.HTTP_404_NOT_FOUND, "not_found", "Požadovaný úvazek nebyl nalezen.")
    assert employment is not None
    return employment


def _get_attendance_for_write(db: Session, *, attendance_id: int) -> models.Attendance:
    row = (
        db.execute(
            select(models.Attendance)
            .options(joinedload(models.Attendance.employment).joinedload(models.Employment.user))
            .where(models.Attendance.id == attendance_id)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise_integration_error(status.HTTP_404_NOT_FOUND, "not_found", "Docházkový záznam nebyl nalezen.")
    assert row is not None
    return row


def _ensure_attendance_in_employment_period(employment: models.Employment, *, day: date) -> None:
    if day < employment.start_date or (employment.end_date is not None and day > employment.end_date):
        raise_integration_error(
            status.HTTP_409_CONFLICT,
            "conflict",
            "Datum neleží v období platnosti vybraného úvazku.",
        )


def _ensure_attendance_month_not_locked(db: Session, *, employment_id: int, day: date) -> None:
    lock = db.execute(
        select(models.AttendanceLock).where(
            models.AttendanceLock.employment_id == employment_id,
            models.AttendanceLock.year == day.year,
            models.AttendanceLock.month == day.month,
        )
    ).scalar_one_or_none()
    if lock is not None:
        raise_integration_error(
            status.HTTP_409_CONFLICT,
            "attendance_locked",
            "Docházka za zvolené období je uzamčena.",
        )


def _ensure_expected_updated_at(
    row: models.Attendance,
    *,
    expected_updated_at: datetime | None,
) -> None:
    if expected_updated_at is None:
        return
    current = row.updated_at.astimezone(UTC)
    if current.replace(microsecond=0) != expected_updated_at.replace(microsecond=0):
        raise_integration_error(
            status.HTTP_409_CONFLICT,
            "conflict",
            "Docházkový záznam byl mezitím změněn.",
        )


@router.get("/health")
def integration_health(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit: None = Depends(rate_limit_dependency("integration-health", 60)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "integration:health")
    get_audit_context(request).row_count = 1
    return {
        "ok": True,
        "service": "dagmar-integration-api",
        "api_version": "v1",
        "contract_version": settings.integration_contract_version,
        "timezone": TIMEZONE,
    }


@router.get("/employments", response_model=ListResponse)
def integration_employments(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
    employment_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    active: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "employments:read")
    _check_requested_scope(auth=auth, db=db, employment_id=employment_id, employee_id=employee_id)
    limit = _normalize_limit(limit)
    cursor_value = decode_cursor(cursor)
    rows = db.execute(select(models.Employment).options(joinedload(models.Employment.user)).order_by(models.Employment.id.asc())).scalars().all()
    rows = _filter_employments_by_scope(rows, auth)
    filtered: list[models.Employment] = []
    period_start = parse_iso_date(date_from, field_name="date_from") if date_from else None
    period_end = parse_iso_date(date_to, field_name="date_to") if date_to else None
    for row in rows:
        if employment_id is not None and row.id != employment_id:
            continue
        if employee_id is not None and row.user_id != employee_id:
            continue
        if active is not None and row.is_active != active:
            continue
        if period_start is not None and row.end_date is not None and row.end_date < period_start:
            continue
        if period_end is not None and row.start_date > period_end:
            continue
        filtered.append(row)
    payload = [_employment_payload(row) for row in filtered]
    if cursor_value is not None:
        last_seen = int(cursor_value.get("cursor_key", 0))
        payload = [row for row in payload if int(row["cursor_key"]) > last_seen]
    return _paginate_records(request=request, records=payload, limit=limit, cursor_key="cursor_key")


@router.get("/shift-plan", response_model=ListResponse)
def integration_shift_plan(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
    date_from: str = Query(...),
    date_to: str = Query(...),
    employment_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    include_locks: bool = False,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "shift_plan:read")
    _check_requested_scope(auth=auth, db=db, employment_id=employment_id, employee_id=employee_id)
    limit = _normalize_limit(limit)
    start, end = _period(date_from, date_to)
    cursor_value = decode_cursor(cursor)
    employments = db.execute(select(models.Employment).options(joinedload(models.Employment.user))).scalars().all()
    employment_map = {
        row.id: row
        for row in _filter_employments_by_scope(employments, auth)
        if (employment_id is None or row.id == employment_id) and (employee_id is None or row.user_id == employee_id)
    }
    rows = db.execute(
        select(models.ShiftPlan)
        .where(models.ShiftPlan.date >= start)
        .where(models.ShiftPlan.date <= end)
        .order_by(models.ShiftPlan.date.asc(), models.ShiftPlan.employment_id.asc(), models.ShiftPlan.id.asc())
    ).scalars().all()
    lock_map = _load_lock_map(db, employment_ids=list(employment_map.keys()), start=start, end=end) if include_locks else {}
    payload: list[dict[str, Any]] = []
    for row in rows:
        employment = employment_map.get(row.employment_id)
        if employment is None:
            continue
        lock = lock_map.get((row.employment_id, row.date.year, row.date.month))
        cursor_key = f"{row.date.isoformat()}:{row.employment_id}:{row.id}"
        payload.append(
            {
                "record_id": cursor_key,
                "shift_plan_id": row.id,
                "employment_id": row.employment_id,
                "employee_id": employment.user_id,
                "date": row.date.isoformat(),
                "planned_arrival_time": row.arrival_time,
                "planned_departure_time": row.departure_time,
                "planned_status": row.status,
                "timezone": TIMEZONE,
                "lock_status": "LOCKED" if lock is not None else "UNLOCKED",
                "last_changed_at": utc_isoformat(row.updated_at or row.created_at),
                "cursor_key": cursor_key,
            }
        )
    if cursor_value is not None:
        last_seen = str(cursor_value.get("cursor_key", ""))
        payload = [row for row in payload if str(row["cursor_key"]) > last_seen]
    return _paginate_records(request=request, records=payload, limit=limit, cursor_key="cursor_key")


@router.get("/attendances", response_model=ListResponse)
def integration_attendances(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
    date_from: str = Query(...),
    date_to: str = Query(...),
    employment_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    include_plan: bool = False,
    include_locks: bool = False,
    include_punches: bool = False,
    include_corrections: bool = False,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "attendance:read")
    _check_requested_scope(auth=auth, db=db, employment_id=employment_id, employee_id=employee_id)
    limit = _normalize_limit(limit)
    start, end = _period(date_from, date_to)
    cursor_value = decode_cursor(cursor)
    employments = db.execute(select(models.Employment).options(joinedload(models.Employment.user))).scalars().all()
    employment_map = {
        row.id: row
        for row in _filter_employments_by_scope(employments, auth)
        if (employment_id is None or row.id == employment_id) and (employee_id is None or row.user_id == employee_id)
    }
    attendance_rows = db.execute(
        select(models.Attendance)
        .where(models.Attendance.date >= start)
        .where(models.Attendance.date <= end)
        .order_by(models.Attendance.date.asc(), models.Attendance.employment_id.asc(), models.Attendance.id.asc())
    ).scalars().all()
    plan_rows = {}
    if include_plan:
        rows = db.execute(
            select(models.ShiftPlan)
            .where(models.ShiftPlan.date >= start)
            .where(models.ShiftPlan.date <= end)
        ).scalars().all()
        plan_rows = {(row.employment_id, row.date): row for row in rows}
    lock_map = _load_lock_map(db, employment_ids=list(employment_map.keys()), start=start, end=end) if include_locks else {}
    payload: list[dict[str, Any]] = []
    for row in attendance_rows:
        employment = employment_map.get(row.employment_id)
        if employment is None:
            continue
        plan = plan_rows.get((row.employment_id, row.date))
        punches = []
        if include_punches:
            if row.arrival_time:
                punches.append(
                    {
                        "event_type": "ARRIVAL",
                        "event_time": row.arrival_time,
                        "source": "derived_from_attendance",
                        "raw_event_available": False,
                    }
                )
            if row.departure_time:
                punches.append(
                    {
                        "event_type": "DEPARTURE",
                        "event_time": row.departure_time,
                        "source": "derived_from_attendance",
                        "raw_event_available": False,
                    }
                )
        lock = lock_map.get((row.employment_id, row.date.year, row.date.month))
        cursor_key = f"{row.date.isoformat()}:{row.employment_id}:{row.id}"
        payload.append(
            {
                "attendance_id": row.id,
                "employment_id": row.employment_id,
                "employee_id": employment.user_id,
                "date": row.date.isoformat(),
                "arrival_time": row.arrival_time,
                "departure_time": row.departure_time,
                "timezone": TIMEZONE,
                "plan": (
                    {
                        "planned_arrival_time": plan.arrival_time,
                        "planned_departure_time": plan.departure_time,
                        "planned_status": plan.status,
                    }
                    if plan is not None and include_plan
                    else None
                ),
                "lock_status": "LOCKED" if lock is not None else "UNLOCKED",
                "punches": punches if include_punches else None,
                "correction_status": "not_tracked" if include_corrections else None,
                "last_changed_at": utc_isoformat(row.updated_at),
                "cursor_key": cursor_key,
            }
        )
    if cursor_value is not None:
        last_seen = str(cursor_value.get("cursor_key", ""))
        payload = [row for row in payload if str(row["cursor_key"]) > last_seen]
    return _paginate_records(request=request, records=payload, limit=limit, cursor_key="cursor_key")


@router.post("/attendances", response_model=IntegrationAttendanceMutationOut, status_code=status.HTTP_201_CREATED)
def create_integration_attendance(
    payload: IntegrationAttendanceCreateIn,
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "attendance:create")
    day = parse_iso_date(payload.date, field_name="date")
    set_attendance_write_audit(
        request,
        operation="attendance:create",
        employment_id=payload.employment_id,
        attendance_date=day,
    )
    _check_requested_scope(auth=auth, db=db, employment_id=payload.employment_id, employee_id=None)
    employment = _get_employment_for_write(db, employment_id=payload.employment_id)
    _ensure_attendance_in_employment_period(employment, day=day)
    _ensure_attendance_month_not_locked(db, employment_id=employment.id, day=day)
    arrival_time, departure_time = _parse_attendance_times(
        arrival_time=payload.arrival_time,
        departure_time=payload.departure_time,
    )

    existing = db.execute(
        select(models.Attendance).where(
            models.Attendance.employment_id == employment.id,
            models.Attendance.date == day,
        )
    ).scalar_one_or_none()
    if existing is not None:
        set_attendance_write_audit(
            request,
            operation="attendance:create",
            attendance_id=existing.id,
            employment_id=existing.employment_id,
            attendance_date=existing.date,
            before_state=_attendance_state(existing),
        )
        raise_integration_error(
            status.HTTP_409_CONFLICT,
            "duplicate_attendance",
            "Docházka pro zadaný úvazek a datum už existuje.",
        )

    row = models.Attendance(
        employment_id=employment.id,
        instance_id=employment.user.instance_id if employment.user is not None else None,
        date=day,
        arrival_time=arrival_time,
        departure_time=departure_time,
    )
    row.employment = employment
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise_integration_error(
            status.HTTP_409_CONFLICT,
            "duplicate_attendance",
            "Docházka pro zadaný úvazek a datum už existuje.",
        )
    db.refresh(row)
    set_attendance_write_audit(
        request,
        operation="attendance:create",
        attendance_id=row.id,
        employment_id=row.employment_id,
        attendance_date=row.date,
        after_state=_attendance_state(row),
    )
    get_audit_context(request).row_count = 1
    return _serialize_attendance_mutation(row)


@router.patch("/attendances/{attendance_id}", response_model=IntegrationAttendanceMutationOut)
def patch_integration_attendance(
    attendance_id: int,
    payload: IntegrationAttendancePatchIn,
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "attendance:update")
    fields_to_update = {"arrival_time", "departure_time"} & set(payload.model_fields_set)
    if not fields_to_update:
        raise_integration_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_attendance_payload",
            "Payload musí obsahovat alespoň jedno zapisovatelné pole docházky.",
        )
    expected_updated_at = _parse_expected_updated_at(payload.expected_updated_at)
    set_attendance_write_audit(
        request,
        operation="attendance:update",
        attendance_id=attendance_id,
        expected_updated_at=expected_updated_at,
    )
    row = _get_attendance_for_write(db, attendance_id=attendance_id)
    employee_id = row.employment.user_id if row.employment is not None else None
    _check_requested_scope(auth=auth, db=db, employment_id=row.employment_id, employee_id=employee_id)
    _ensure_attendance_month_not_locked(db, employment_id=row.employment_id, day=row.date)
    _ensure_expected_updated_at(row, expected_updated_at=expected_updated_at)
    arrival_time, departure_time = _parse_attendance_times(
        arrival_time=payload.arrival_time,
        departure_time=payload.departure_time,
    )
    before_state = _attendance_state(row)
    if "arrival_time" in fields_to_update:
        row.arrival_time = arrival_time
    if "departure_time" in fields_to_update:
        row.departure_time = departure_time
    db.add(row)
    db.commit()
    db.refresh(row)
    set_attendance_write_audit(
        request,
        operation="attendance:update",
        attendance_id=row.id,
        employment_id=row.employment_id,
        attendance_date=row.date,
        expected_updated_at=expected_updated_at,
        before_state=before_state,
        after_state=_attendance_state(row),
    )
    get_audit_context(request).row_count = 1
    return _serialize_attendance_mutation(row)


@router.delete("/attendances/{attendance_id}", response_model=IntegrationAttendanceMutationOut)
def delete_integration_attendance(
    attendance_id: int,
    request: Request,
    payload: IntegrationAttendanceDeleteIn | None = Body(default=None),
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "attendance:delete")
    expected_updated_at = _parse_expected_updated_at(payload.expected_updated_at if payload is not None else None)
    set_attendance_write_audit(
        request,
        operation="attendance:delete",
        attendance_id=attendance_id,
        expected_updated_at=expected_updated_at,
    )
    row = _get_attendance_for_write(db, attendance_id=attendance_id)
    employee_id = row.employment.user_id if row.employment is not None else None
    _check_requested_scope(auth=auth, db=db, employment_id=row.employment_id, employee_id=employee_id)
    _ensure_attendance_month_not_locked(db, employment_id=row.employment_id, day=row.date)
    _ensure_expected_updated_at(row, expected_updated_at=expected_updated_at)
    before_state = _attendance_state(row)
    response_payload = _serialize_attendance_mutation(row, deleted=True)
    set_attendance_write_audit(
        request,
        operation="attendance:delete",
        attendance_id=row.id,
        employment_id=row.employment_id,
        attendance_date=row.date,
        expected_updated_at=expected_updated_at,
        before_state=before_state,
        after_state={"deleted": True},
    )
    db.delete(row)
    db.commit()
    get_audit_context(request).row_count = 1
    return response_payload


@router.get("/punches", response_model=ListResponse)
def integration_punches(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
    date_from: str = Query(...),
    date_to: str = Query(...),
    employment_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "punches:read")
    _check_requested_scope(auth=auth, db=db, employment_id=employment_id, employee_id=employee_id)
    if event_type not in (None, "ARRIVAL", "DEPARTURE"):
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "invalid_request", "Pole event_type musí být ARRIVAL nebo DEPARTURE.")
    limit = _normalize_limit(limit)
    start, end = _period(date_from, date_to)
    cursor_value = decode_cursor(cursor)
    employments = db.execute(select(models.Employment)).scalars().all()
    employment_map = {
        row.id: row
        for row in _filter_employments_by_scope(employments, auth)
        if (employment_id is None or row.id == employment_id) and (employee_id is None or row.user_id == employee_id)
    }
    attendance_rows = db.execute(
        select(models.Attendance)
        .where(models.Attendance.date >= start)
        .where(models.Attendance.date <= end)
        .order_by(models.Attendance.date.asc(), models.Attendance.employment_id.asc(), models.Attendance.id.asc())
    ).scalars().all()
    payload: list[dict[str, Any]] = []
    for row in attendance_rows:
        employment = employment_map.get(row.employment_id)
        if employment is None:
            continue
        base = {
            "attendance_id": row.id,
            "employment_id": row.employment_id,
            "employee_id": employment.user_id,
            "date": row.date.isoformat(),
            "source": "derived_from_attendance",
            "raw_event_available": False,
        }
        if row.arrival_time and event_type in (None, "ARRIVAL"):
            cursor_key = f"{row.date.isoformat()}:{row.employment_id}:ARRIVAL:{row.id}"
            payload.append(
                {
                    **base,
                    "event_type": "ARRIVAL",
                    "event_time": row.arrival_time,
                    "cursor_key": cursor_key,
                }
            )
        if row.departure_time and event_type in (None, "DEPARTURE"):
            cursor_key = f"{row.date.isoformat()}:{row.employment_id}:DEPARTURE:{row.id}"
            payload.append(
                {
                    **base,
                    "event_type": "DEPARTURE",
                    "event_time": row.departure_time,
                    "cursor_key": cursor_key,
                }
            )
    payload.sort(key=lambda item: str(item["cursor_key"]))
    if cursor_value is not None:
        last_seen = str(cursor_value.get("cursor_key", ""))
        payload = [row for row in payload if str(row["cursor_key"]) > last_seen]
    return _paginate_records(request=request, records=payload, limit=limit, cursor_key="cursor_key")


@router.get("/locks", response_model=ListResponse)
def integration_locks(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-data", 120)),
    db: Session = Depends(get_db),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    date_from: str | None = None,
    date_to: str | None = None,
    employment_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "locks:read")
    _check_requested_scope(auth=auth, db=db, employment_id=employment_id, employee_id=employee_id)
    limit = _normalize_limit(limit)
    cursor_value = decode_cursor(cursor)
    if year is None and month is None and (date_from is None or date_to is None):
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "invalid_request", "U zámků zadejte year+month nebo date_from+date_to.")
    start = parse_iso_date(date_from, field_name="date_from") if date_from else None
    end = parse_iso_date(date_to, field_name="date_to") if date_to else None
    employments = db.execute(select(models.Employment)).scalars().all()
    employment_map = {
        row.id: row
        for row in _filter_employments_by_scope(employments, auth)
        if (employment_id is None or row.id == employment_id) and (employee_id is None or row.user_id == employee_id)
    }
    rows = db.execute(
        select(models.AttendanceLock).order_by(
            models.AttendanceLock.year.asc(),
            models.AttendanceLock.month.asc(),
            models.AttendanceLock.employment_id.asc(),
            models.AttendanceLock.id.asc(),
        )
    ).scalars().all()
    payload: list[dict[str, Any]] = []
    for row in rows:
        employment = employment_map.get(row.employment_id)
        if employment is None:
            continue
        if year is not None and row.year != year:
            continue
        if month is not None and row.month != month:
            continue
        month_date = date(row.year, row.month, 1)
        if start is not None and month_date < date(start.year, start.month, 1):
            continue
        if end is not None and month_date > date(end.year, end.month, 1):
            continue
        cursor_key = f"{row.year:04d}-{row.month:02d}:{row.employment_id}:{row.id}"
        payload.append(
            {
                "lock_id": row.id,
                "employment_id": row.employment_id,
                "employee_id": employment.user_id,
                "year": row.year,
                "month": row.month,
                "locked_at": utc_isoformat(row.locked_at),
                "locked_by": row.locked_by,
                "is_locked": True,
                "last_changed_at": utc_isoformat(row.locked_at),
                "cursor_key": cursor_key,
            }
        )
    if cursor_value is not None:
        last_seen = str(cursor_value.get("cursor_key", ""))
        payload = [row for row in payload if str(row["cursor_key"]) > last_seen]
    return _paginate_records(request=request, records=payload, limit=limit, cursor_key="cursor_key")


@router.get("/openapi.json", include_in_schema=False)
def integration_openapi(
    request: Request,
    auth: IntegrationAuth = Depends(require_integration_auth),
    _limit_guard: None = Depends(rate_limit_dependency("integration-openapi", 10)),
) -> dict[str, Any]:
    request.state.integration_rate_key = f"client:{auth.client.id}"
    _ensure_scope(auth, "openapi:read")
    schema = get_openapi(
        title="Dagmar Integration API",
        version="1.0.0",
        routes=router.routes,
        description="Read-only integrační API pro systém Dagmar.",
    )
    get_audit_context(request).row_count = len(schema.get("paths", {}))
    return schema
