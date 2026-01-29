from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import EmploymentTemplate, Instance, InstanceStatus
from ...security.csrf import require_csrf
from ..deps import get_db, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin-instances"])


class InstanceOut(BaseModel):
    id: str
    client_type: str
    device_fingerprint: str
    status: Literal["PENDING", "ACTIVE", "REVOKED", "DEACTIVATED"]
    display_name: str | None = None
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


def _normalize_employment_template(value: str | None) -> EmploymentTemplateLiteral:
    if value == EmploymentTemplate.HPP.value:
        return "HPP"
    return "DPP_DPC"


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

    # Token issuance is handled by claim-token endpoint; activation only flips state + name.
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
