# ruff: noqa: B008
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import distinct, or_, select
from sqlalchemy.orm import Session, joinedload

from ...db.models import Attendance, Employment
from ...db.session import get_db
from ...utils.slugify import filename_safe
from ..deps import require_admin

router = APIRouter(tags=["admin"])


def _month_range(month_yyyy_mm: str) -> tuple[date, date]:
    try:
        y_str, m_str = month_yyyy_mm.split("-", 1)
        y = int(y_str)
        m = int(m_str)
        if not (1 <= m <= 12):
            raise ValueError
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid month. Expected YYYY-MM") from exc

    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start, end


def _employment_display_name(employment: Employment) -> str:
    user_name = employment.user.name if employment.user else f"Uzivatel {employment.user_id}"
    type_label = "HPP" if employment.employment_type == "HPP" else "DPP_DPC"
    return f"{user_name} - {type_label} - {employment.title}"


def _csv_for_employment(
    *,
    db: Session,
    employment: Employment,
    start: date,
    end: date,
) -> bytes:
    q = (
        select(Attendance)
        .where(Attendance.employment_id == employment.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    )
    rows = db.execute(q).scalars().all()

    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    w.writerow(["zamestnanec", "uvazek", "typ_uvazku", "datum", "prichod", "odchod"])
    user_name = employment.user.name if employment.user else f"Uzivatel {employment.user_id}"
    for row in rows:
        w.writerow(
            [
                user_name,
                employment.title,
                employment.employment_type,
                row.date.isoformat(),
                row.arrival_time or "",
                row.departure_time or "",
            ]
        )

    return buf.getvalue().encode("utf-8")


def _iter_bytes(data: bytes, chunk_size: int = 64 * 1024) -> Iterable[bytes]:
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


def _load_relevant_employments(db: Session, start: date, end: date) -> list[Employment]:
    candidates = (
        db.execute(
            select(Employment)
            .options(joinedload(Employment.user))
            .where(
                or_(
                    Employment.end_date.is_(None),
                    Employment.end_date >= start,
                )
            )
            .where(Employment.start_date < end)
            .order_by(Employment.start_date.asc(), Employment.id.asc())
        )
        .scalars()
        .all()
    )
    attendance_ids = db.execute(
        select(distinct(Attendance.employment_id)).where(Attendance.date >= start, Attendance.date < end)
    ).scalars().all()
    attendance_id_set = set(attendance_ids)
    relevant = [employment for employment in candidates if employment.is_active or employment.id in attendance_id_set]
    seen = {employment.id for employment in relevant}
    if attendance_id_set - seen:
        extra = (
            db.execute(
                select(Employment)
                .options(joinedload(Employment.user))
                .where(Employment.id.in_(attendance_id_set - seen))
            )
            .scalars()
            .all()
        )
        relevant.extend(extra)
    relevant.sort(key=lambda item: (item.user.name if item.user else "", item.start_date, item.id))
    return relevant


@router.get("/api/v1/admin/export")
def export_csv_or_zip(
    month: str = Query(..., description="YYYY-MM"),
    employment_id: int | None = Query(None),
    bulk: bool | None = Query(False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    start, end = _month_range(month)

    if bulk and employment_id:
        raise HTTPException(status_code=400, detail="Use either bulk=true or employment_id, not both")

    if not bulk:
        if not employment_id:
            raise HTTPException(status_code=400, detail="employment_id is required unless bulk=true")

        employment = (
            db.execute(select(Employment).options(joinedload(Employment.user)).where(Employment.id == employment_id))
            .scalars()
            .first()
        )
        if not employment:
            raise HTTPException(status_code=404, detail="Employment not found")

        display = _employment_display_name(employment)
        fname = f"{filename_safe(display)}_{month}.csv"
        content = _csv_for_employment(db=db, employment=employment, start=start, end=end)

        return StreamingResponse(
            _iter_bytes(content),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    employments = _load_relevant_employments(db, start, end)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        for employment in employments:
            display = _employment_display_name(employment)
            fname = f"{filename_safe(display)}_{month}.csv"
            csv_bytes = _csv_for_employment(db=db, employment=employment, start=start, end=end)
            z.writestr(fname, csv_bytes)

    zip_bytes = zip_buf.getvalue()
    zip_name = f"export_{month}.zip"

    return StreamingResponse(
        _iter_bytes(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )
