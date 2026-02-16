from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    Attendance,
    AttendanceLock,
    EmploymentTemplate,
    Instance,
    InstanceStatus,
    ShiftPlan,
    ShiftPlanMonthInstance,
)
from ...security.csrf import require_csrf
from ..deps import get_db, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin-instances"])


class InstanceOut(BaseModel):
    id: str
    client_type: str
    device_fingerprint: str
    status: Literal["PENDING", "ACTIVE", "REVOKED", "DEACTIVATED"]
    display_name: str | None = None
    profile_instance_id: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    activated_at: datetime | None = None
    revoked_at: datetime | None = None
    deactivated_at: datetime | None = None
    employment_template: Literal["DPP_DPC", "HPP"] = "DPP_DPC"


class ActivateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    employment_template: Literal["DPP_DPC", "HPP"] = "DPP_DPC"


class RenameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class SetTemplateIn(BaseModel):
    employment_template: Literal["DPP_DPC", "HPP"]


EmploymentTemplateLiteral = Literal["DPP_DPC", "HPP"]
InstanceIdSet = set[str]


class MergeInstancesIn(BaseModel):
    target_id: str = Field(..., min_length=1)
    source_ids: list[str] = Field(default_factory=list, min_length=1)


class MergeInstancesOut(BaseModel):
    ok: bool = True
    merged_count: int = 0


def _normalize_employment_template(value: str | None) -> EmploymentTemplateLiteral:
    if value == EmploymentTemplate.HPP.value:
        return "HPP"
    return "DPP_DPC"


def _merge_attendance(db: Session, source_id: str, target_id: str) -> int:
    moved = 0
    rows = db.execute(select(Attendance).where(Attendance.instance_id == source_id)).scalars().all()
    for row in rows:
        existing = db.execute(
            select(Attendance).where(
                Attendance.instance_id == target_id,
                Attendance.date == row.date,
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.delete(row)
            continue
        row.instance_id = target_id
        db.add(row)
        moved += 1
    return moved


def _merge_shift_plan(db: Session, source_id: str, target_id: str) -> int:
    moved = 0
    rows = db.execute(select(ShiftPlan).where(ShiftPlan.instance_id == source_id)).scalars().all()
    for row in rows:
        existing = db.execute(
            select(ShiftPlan).where(
                ShiftPlan.instance_id == target_id,
                ShiftPlan.date == row.date,
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.delete(row)
            continue
        row.instance_id = target_id
        db.add(row)
        moved += 1
    return moved


def _merge_shift_plan_month_instances(db: Session, source_id: str, target_id: str) -> int:
    moved = 0
    rows = db.execute(
        select(ShiftPlanMonthInstance).where(ShiftPlanMonthInstance.instance_id == source_id)
    ).scalars().all()
    for row in rows:
        existing = db.execute(
            select(ShiftPlanMonthInstance).where(
                ShiftPlanMonthInstance.instance_id == target_id,
                ShiftPlanMonthInstance.year == row.year,
                ShiftPlanMonthInstance.month == row.month,
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.delete(row)
            continue
        row.instance_id = target_id
        db.add(row)
        moved += 1
    return moved


def _merge_attendance_locks(db: Session, source_id: str, target_id: str) -> int:
    moved = 0
    rows = db.execute(
        select(AttendanceLock).where(AttendanceLock.instance_id == source_id)
    ).scalars().all()
    for row in rows:
        existing = db.execute(
            select(AttendanceLock).where(
                AttendanceLock.instance_id == target_id,
                AttendanceLock.year == row.year,
                AttendanceLock.month == row.month,
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.delete(row)
            continue
        row.instance_id = target_id
        db.add(row)
        moved += 1
    return moved


@router.get("/instances", response_model=list[InstanceOut])
def list_instances(
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    q = select(Instance).order_by(Instance.created_at.desc())
    items = db.execute(q).scalars().all()
    return [
        InstanceOut(
            id=i.id,
            client_type=i.client_type,
            device_fingerprint=i.device_fingerprint,
            status=i.status.value,
            display_name=i.display_name,
            profile_instance_id=i.profile_instance_id,
            created_at=i.created_at,
            last_seen_at=i.last_seen_at,
            activated_at=i.activated_at,
            revoked_at=i.revoked_at,
            deactivated_at=i.deactivated_at,
            employment_template=_normalize_employment_template(i.employment_template),
        )
        for i in items
    ]


@router.post("/instances/{instance_id}/activate")
def activate_instance(
    instance_id: str,
    payload: ActivateIn,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if inst.status == InstanceStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Instance is revoked")

    inst.display_name = payload.display_name.strip()
    inst.status = InstanceStatus.ACTIVE
    inst.employment_template = payload.employment_template

    # Re-activation clears previous deactivation timestamp.
    inst.deactivated_at = None

    # Token issuance is handled by claim token endpoint; activation only flips state + name.
    inst.activated_at = datetime.now(UTC)

    db.add(inst)
    db.commit()

    return {"ok": True}


@router.post("/instances/{instance_id}/rename")
def rename_instance(
    instance_id: str,
    payload: RenameIn,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if inst.status != InstanceStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Only ACTIVE instances can be renamed")

    inst.display_name = payload.display_name.strip()
    db.add(inst)
    db.commit()

    return {"ok": True}


@router.post("/instances/{instance_id}/set-template")
def set_template(
    instance_id: str,
    payload: SetTemplateIn,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    inst.employment_template = payload.employment_template
    db.add(inst)
    db.commit()
    return {"ok": True}


@router.post("/instances/merge", response_model=MergeInstancesOut)
def merge_instances(
    payload: MergeInstancesIn,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    target_id = payload.target_id.strip()
    source_ids: list[str] = []
    seen: InstanceIdSet = set()
    for iid in payload.source_ids:
        if not iid:
            continue
        if iid == target_id:
            continue
        if iid in seen:
            continue
        seen.add(iid)
        source_ids.append(iid)

    if not target_id or not source_ids:
        raise HTTPException(status_code=400, detail="Provide target_id and at least one source_id")

    target = db.get(Instance, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target instance not found")
    if target.status != InstanceStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Target instance must be ACTIVE")
    if target.profile_instance_id:
        raise HTTPException(status_code=409, detail="Target instance is already merged")

    sources = db.execute(select(Instance).where(Instance.id.in_(source_ids))).scalars().all()
    by_id = {s.id: s for s in sources}
    missing = [iid for iid in source_ids if iid not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="Some source instances were not found")

    for src in sources:
        if src.status != InstanceStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="All source instances must be ACTIVE")
        if src.profile_instance_id and src.profile_instance_id != target.id:
            raise HTTPException(status_code=409, detail="Source instance is already merged")

    for src in sources:
        _merge_attendance(db, src.id, target.id)
        _merge_shift_plan(db, src.id, target.id)
        _merge_shift_plan_month_instances(db, src.id, target.id)
        _merge_attendance_locks(db, src.id, target.id)
        src.profile_instance_id = target.id
        db.add(src)

    db.commit()
    return MergeInstancesOut(ok=True, merged_count=len(sources))


@router.post("/instances/{instance_id}/revoke")
def revoke_instance(
    instance_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    inst.status = InstanceStatus.REVOKED
    inst.revoked_at = datetime.now(UTC)

    # Clearing token hash prevents further use even if client still has token.
    inst.token_hash = None
    inst.token_issued_at = None

    db.add(inst)
    db.commit()

    return {"ok": True}


@router.post("/instances/{instance_id}/deactivate")
def deactivate_instance(
    instance_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    inst.status = InstanceStatus.DEACTIVATED
    inst.deactivated_at = datetime.now(UTC)
    inst.token_hash = None
    inst.token_issued_at = None

    db.add(inst)
    db.commit()
    return {"ok": True}


@router.delete("/instances/{instance_id}")
def delete_instance(
    instance_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _: None = Depends(require_csrf),
):
    # Special-case bulk delete endpoint (to avoid path clash with {instance_id}).
    if instance_id == "pending":
        pending = db.scalars(select(Instance).where(Instance.status == InstanceStatus.PENDING)).all()
        deleted = 0
        for pending_inst in pending:
            db.delete(pending_inst)
            deleted += 1
        db.commit()
        return {"ok": True, "deleted": deleted}

    inst = db.get(Instance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    # If not revoked yet, revoke first to invalidate tokens before deletion.
    if inst.status != InstanceStatus.REVOKED:
        inst.status = InstanceStatus.REVOKED
        inst.revoked_at = datetime.now(UTC)
        inst.token_hash = None
        inst.token_issued_at = None
        db.add(inst)

    db.delete(inst)
    db.commit()
    return {"ok": True}


@router.delete("/instances/pending")
def delete_pending_instances(
    _admin: Annotated[dict, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    pending = db.scalars(select(Instance).where(Instance.status == InstanceStatus.PENDING)).all()
    deleted = 0
    for inst in pending:
        db.delete(inst)
        deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}
