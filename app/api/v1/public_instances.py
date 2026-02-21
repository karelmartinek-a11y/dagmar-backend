# ruff: noqa: B008
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AppSettings, ClientType, Instance, InstanceStatus
from app.db.session import get_db
from app.security.tokens import rotate_instance_token

router = APIRouter(tags=["public-instances"])


class RegisterInstanceIn(BaseModel):
    client_type: ClientType
    device_fingerprint: str = Field(min_length=1, max_length=128)
    device_info: dict | None = None
    display_name: str | None = Field(default=None, max_length=128)


class RegisterInstanceOut(BaseModel):
    instance_id: str
    status: str


class InstanceStatusOut(BaseModel):
    status: str
    display_name: str | None = None
    employment_template: str | None = None
    afternoon_cutoff: str | None = None


class ClaimTokenOut(BaseModel):
    instance_token: str
    display_name: str | None = None


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _get_cutoff(db: Session) -> str:
    row = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if row is None:
        return "17:00"
    return _minutes_to_hhmm(row.afternoon_cutoff_minutes)


@router.post("/api/v1/instances/register", response_model=RegisterInstanceOut)
def register_instance(payload: RegisterInstanceIn, db: Session = Depends(get_db)) -> RegisterInstanceOut:
    now = datetime.now(UTC)
    inst = Instance(
        id=str(uuid4()),
        client_type=payload.client_type,
        device_fingerprint=payload.device_fingerprint.strip(),
        device_info_json=(json.dumps(payload.device_info, ensure_ascii=False) if payload.device_info else None),
        status=InstanceStatus.PENDING,
        display_name=payload.display_name.strip() if payload.display_name else None,
        created_at=now,
        last_seen_at=now,
    )
    db.add(inst)
    db.commit()
    return RegisterInstanceOut(instance_id=inst.id, status=inst.status.value)


@router.get("/api/v1/instances/{instance_id}/status", response_model=InstanceStatusOut)
def get_instance_status(instance_id: str, db: Session = Depends(get_db)) -> InstanceStatusOut:
    inst = db.get(Instance, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    inst.last_seen_at = datetime.now(UTC)
    db.add(inst)
    db.commit()

    out = InstanceStatusOut(status=inst.status.value)
    if inst.status == InstanceStatus.ACTIVE:
        out.display_name = inst.display_name
        out.employment_template = inst.employment_template
        out.afternoon_cutoff = _get_cutoff(db)
    return out


@router.post("/api/v1/instances/{instance_id}/claim-token", response_model=ClaimTokenOut)
def claim_instance_token(instance_id: str, db: Session = Depends(get_db)) -> ClaimTokenOut:
    inst = db.get(Instance, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    if inst.status != InstanceStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Instance is not active",
        )

    token = rotate_instance_token(db, inst)
    db.commit()
    return ClaimTokenOut(instance_token=token, display_name=inst.display_name)
