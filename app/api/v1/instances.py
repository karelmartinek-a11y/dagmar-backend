from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import AppSettings, EmploymentTemplate, Instance, InstanceStatus
from app.security.rate_limit import rate_limit
from app.security.tokens import issue_instance_token_once, rotate_instance_token

router = APIRouter(prefix="/api/v1/instances", tags=["instances"])


class InstanceRegisterIn(BaseModel):
    client_type: Literal["ANDROID", "WEB"]
    device_fingerprint: str = Field(min_length=1, max_length=200)
    device_info: Optional[dict[str, Any]] = None
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class InstanceRegisterOut(BaseModel):
    instance_id: str
    status: Literal["PENDING", "ACTIVE", "REVOKED", "DEACTIVATED"]


class InstanceStatusOutPending(BaseModel):
    status: Literal["PENDING"]


class InstanceStatusOutActive(BaseModel):
    status: Literal["ACTIVE"]
    display_name: str
    employment_template: Literal["DPP_DPC", "HPP"]
    afternoon_cutoff: str


class InstanceStatusOutRevoked(BaseModel):
    status: Literal["REVOKED"]


class InstanceStatusOutDeactivated(BaseModel):
    status: Literal["DEACTIVATED"]


InstanceStatusOut = (
    InstanceStatusOutPending | InstanceStatusOutActive | InstanceStatusOutRevoked | InstanceStatusOutDeactivated
)


class ClaimTokenOut(BaseModel):
    instance_token: str
    display_name: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _minutes_to_hhmm(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _get_settings(db: Session) -> AppSettings:
    st = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if st is None:
        st = AppSettings(id=1, afternoon_cutoff_minutes=17 * 60)
        db.add(st)
        db.commit()
        db.refresh(st)
    return st


@router.post("/register", response_model=InstanceRegisterOut)
@rate_limit("10/minute")
def register_instance(
    payload: InstanceRegisterIn, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Register a new instance.

    Requirements:
    - If online, client calls this on first launch.
    - Instance starts as PENDING and must be activated by admin.
    """

    display_name = payload.display_name.strip() if payload.display_name else None
    if payload.display_name is not None and not display_name:
        raise HTTPException(status_code=400, detail="display_name required")
    if display_name is None:
        raise HTTPException(status_code=400, detail="display_name required")

    rows = (
        db.execute(
            select(Instance)
            .where(
                Instance.client_type == payload.client_type,
                Instance.device_fingerprint == payload.device_fingerprint,
            )
            .order_by(Instance.created_at.desc())
        )
        .scalars()
        .all()
    )

    if any(r.status == InstanceStatus.DEACTIVATED for r in rows):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Instance is deactivated")

    reusable = next((r for r in rows if r.status in (InstanceStatus.ACTIVE, InstanceStatus.PENDING)), None)
    if reusable:
        reusable.last_seen_at = _utcnow()
        if not reusable.employment_template:
            reusable.employment_template = EmploymentTemplate.DPP_DPC.value
        if display_name:
            # Always update na nejnovější jméno od klienta (uživatel může opravit).
            reusable.display_name = display_name
        if payload.device_info:
            reusable.device_info_json = json.dumps(payload.device_info)
        db.add(reusable)
        db.commit()
        return InstanceRegisterOut(instance_id=reusable.id, status=reusable.status.value)

    inst = Instance(
        id=str(uuid.uuid4()),
        client_type=payload.client_type,
        device_fingerprint=payload.device_fingerprint,
        device_info_json=json.dumps(payload.device_info) if payload.device_info else None,
        status=InstanceStatus.PENDING,
        display_name=display_name,
        employment_template=EmploymentTemplate.DPP_DPC.value,
        created_at=_utcnow(),
        last_seen_at=_utcnow(),
    )
    db.add(inst)
    db.commit()

    return InstanceRegisterOut(instance_id=inst.id, status=inst.status.value)


@router.get("/{instance_id}/status", response_model=InstanceStatusOut)
@rate_limit("60/minute")
def get_status(instance_id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    inst = db.get(Instance, instance_id)
    if inst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found")

    inst.last_seen_at = _utcnow()
    db.add(inst)
    db.commit()

    if inst.status == InstanceStatus.PENDING:
        return InstanceStatusOutPending(status="PENDING")
    if inst.status == InstanceStatus.REVOKED:
        return InstanceStatusOutRevoked(status="REVOKED")
    if inst.status == InstanceStatus.DEACTIVATED:
        return InstanceStatusOutDeactivated(status="DEACTIVATED")

    # ACTIVE
    if not inst.display_name:
        # Defensive: ACTIVE bez jména -> doplníme náhradní label a pustíme dál.
        inst.display_name = f"Zařízení {inst.id[:8]}"
        db.add(inst)
        db.commit()

    settings = _get_settings(db)
    return InstanceStatusOutActive(
        status="ACTIVE",
        display_name=inst.display_name,
        employment_template=inst.employment_template or EmploymentTemplate.DPP_DPC.value,
        afternoon_cutoff=_minutes_to_hhmm(settings.afternoon_cutoff_minutes),
    )


@router.post("/{instance_id}/claim-token", response_model=ClaimTokenOut)
@rate_limit("20/minute")
def claim_token(instance_id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    """Client periodically polls this endpoint after activation.

    Security rule:
    - Token is issued only when instance is ACTIVE.
    - Token is issued once (idempotent) unless rotated by admin logic (not implemented).
    """

    inst = db.get(Instance, instance_id)
    if inst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found")

    # Update last_seen regardless; also a target for rate-limiting.
    inst.last_seen_at = _utcnow()
    db.add(inst)

    if inst.status != InstanceStatus.ACTIVE:
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Instance not active")

    if not inst.display_name:
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Instance misconfigured")

    # Issue token (rotating if already issued) so že klient může token znovu získat po ztrátě.
    token = issue_instance_token_once(db, inst)
    if token is None:
        token = rotate_instance_token(db, inst)
    db.commit()

    return ClaimTokenOut(instance_token=token, display_name=inst.display_name)
