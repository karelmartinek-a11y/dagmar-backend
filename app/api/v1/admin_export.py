# ruff: noqa: B008
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Attendance, Instance
from ...db.session import get_db
from ...utils.slugify import filename_safe
from ..deps import require_admin

router = APIRouter(tags=["admin"])


def _month_range(month_yyyy_mm: str) -> tuple[date, date]:
    """Return [start, end) date range for given YYYY-MM."""
    try:
        y_str, m_str = month_yyyy_mm.split("-", 1)
        y = int(y_str)
        m = int(m_str)
        if not (1 <= m <= 12):
            raise ValueError
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid month. Expected YYYY-MM") from e

    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start, end


def _csv_for_instance(
    *,
    db: Session,
    instance: Instance,
    start: date,
    end: date,
) -> bytes:
    """Generate CSV bytes for one instance in month range.

    CSV format:
      datum,prichod,odchod
      YYYY-MM-DD,HH:MM,HH:MM

    arrival/departure can be blank.
    """
    q = (
        select(Attendance)
        .where(Attendance.instance_id == instance.id)
        .where(Attendance.date >= start)
        .where(Attendance.date < end)
        .order_by(Attendance.date.asc())
    )
    rows = db.execute(q).scalars().all()

    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    w.writerow(["datum", "prichod", "odchod"])
    for r in rows:
        w.writerow(
            [
                r.date.isoformat(),
                r.arrival_time or "",
                r.departure_time or "",
            ]
        )

    return buf.getvalue().encode("utf-8")


def _iter_bytes(data: bytes, chunk_size: int = 64 * 1024) -> Iterable[bytes]:
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


@router.get("/api/v1/admin/export")
def export_csv_or_zip(
    month: str = Query(..., description="YYYY-MM"),
    instance_id: str | None = Query(None),
    bulk: bool | None = Query(False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Export attendance.

    - Individual: /api/v1/admin/export?month=YYYY-MM&instance_id=...
      -> returns CSV download

    - Bulk: /api/v1/admin/export?month=YYYY-MM&bulk=true
      -> returns ZIP with multiple CSV

    Naming:
      {nazev_instance}_{YYYY-MM}.csv (no diacritics, spaces -> _)
    """

    start, end = _month_range(month)

    if bulk and instance_id:
        raise HTTPException(status_code=400, detail="Use either bulk=true or instance_id, not both")

    if not bulk:
        if not instance_id:
            raise HTTPException(status_code=400, detail="instance_id is required unless bulk=true")

        instance = db.get(Instance, instance_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Instance not found")

        display = instance.display_name or f"instance_{instance.id}"
        fname = f"{filename_safe(display)}_{month}.csv"
        content = _csv_for_instance(db=db, instance=instance, start=start, end=end)

        return StreamingResponse(
            _iter_bytes(content),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # bulk=true
    instances = db.execute(select(Instance).order_by(Instance.created_at.asc())).scalars().all()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        for inst in instances:
            display = inst.display_name or f"instance_{inst.id}"
            fname = f"{filename_safe(display)}_{month}.csv"
            csv_bytes = _csv_for_instance(db=db, instance=inst, start=start, end=end)
            z.writestr(fname, csv_bytes)

    zip_bytes = zip_buf.getvalue()
    zip_name = f"export_{month}.zip"

    return StreamingResponse(
        _iter_bytes(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )
