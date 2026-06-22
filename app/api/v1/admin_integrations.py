# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_admin
from app.api.integration_common import utc_isoformat
from app.config import Settings, get_settings
from app.db import models
from app.db.session import get_db
from app.security.csrf import require_csrf
from app.security.integration_tokens import build_token_record, generate_integration_token

router = APIRouter(prefix="/api/v1/admin/integrations", tags=["admin-integrations"])


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Pole expires_at musí být ve formátu ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class IntegrationClientOut(BaseModel):
    id: int
    name: str
    status: str
    scopes: list[str]
    allowed_employment_ids: list[int]
    allowed_employee_ids: list[int]
    ip_allowlist: list[str]
    expires_at: str | None
    last_used_at: str | None
    created_at: str
    updated_at: str
    created_by: str | None
    active_secret_fingerprint: str | None = None
    active_secret_last4: str | None = None


class IntegrationClientCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    scopes: list[str] = Field(default_factory=list)
    allowed_employment_ids: list[int] = Field(default_factory=list)
    allowed_employee_ids: list[int] = Field(default_factory=list)
    ip_allowlist: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    created_by: str | None = Field(default=None, max_length=160)


class IntegrationClientUpdateIn(BaseModel):
    scopes: list[str] | None = None
    allowed_employment_ids: list[int] | None = None
    allowed_employee_ids: list[int] | None = None
    ip_allowlist: list[str] | None = None
    expires_at: str | None = None


class IntegrationClientSecretOut(BaseModel):
    client: IntegrationClientOut
    plaintext_token: str


def _serialize_client(client: models.IntegrationClient) -> IntegrationClientOut:
    active_secret = next((item for item in sorted(client.secrets, key=lambda row: row.id, reverse=True) if item.revoked_at is None), None)
    return IntegrationClientOut(
        id=client.id,
        name=client.name,
        status=client.status,
        scopes=list(client.scopes or []),
        allowed_employment_ids=[int(item) for item in (client.allowed_employment_ids or [])],
        allowed_employee_ids=[int(item) for item in (client.allowed_employee_ids or [])],
        ip_allowlist=[str(item) for item in (client.ip_allowlist or [])],
        expires_at=utc_isoformat(client.expires_at),
        last_used_at=utc_isoformat(client.last_used_at),
        created_at=utc_isoformat(client.created_at) or "",
        updated_at=utc_isoformat(client.updated_at) or "",
        created_by=client.created_by,
        active_secret_fingerprint=active_secret.token_fingerprint if active_secret is not None else None,
        active_secret_last4=active_secret.token_last4 if active_secret is not None else None,
    )


def _client_query(db: Session):
    return db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .order_by(models.IntegrationClient.name.asc(), models.IntegrationClient.id.asc())
    ).scalars().all()


@router.get("/clients", response_model=list[IntegrationClientOut])
def list_integration_clients(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[IntegrationClientOut]:
    return [_serialize_client(client) for client in _client_query(db)]


@router.post("/clients", response_model=IntegrationClientSecretOut)
def create_integration_client(
    payload: IntegrationClientCreateIn,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IntegrationClientSecretOut:
    existing = db.execute(select(models.IntegrationClient).where(models.IntegrationClient.name == payload.name.strip())).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Integrační klient se stejným názvem už existuje.")

    client = models.IntegrationClient(
        name=payload.name.strip(),
        status=models.IntegrationClientStatus.ACTIVE.value,
        scopes=sorted(set(item.strip() for item in payload.scopes if item.strip())),
        allowed_employment_ids=sorted(set(int(item) for item in payload.allowed_employment_ids)),
        allowed_employee_ids=sorted(set(int(item) for item in payload.allowed_employee_ids)),
        ip_allowlist=sorted(set(item.strip() for item in payload.ip_allowlist if item.strip())),
        expires_at=_parse_optional_datetime(payload.expires_at),
        created_by=(payload.created_by or "").strip() or None,
    )
    db.add(client)
    db.flush()

    plaintext = generate_integration_token(settings)
    token_record = build_token_record(plaintext)
    secret = models.IntegrationClientSecret(
        client_id=client.id,
        token_hash=token_record.token_hash,
        token_prefix=token_record.token_prefix,
        token_last4=token_record.token_last4,
        token_fingerprint=token_record.token_fingerprint,
    )
    db.add(secret)
    db.commit()
    db.refresh(client)
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client.id)
    ).scalar_one()
    return IntegrationClientSecretOut(client=_serialize_client(client), plaintext_token=plaintext)


@router.post("/clients/{client_id}/rotate", response_model=IntegrationClientSecretOut)
def rotate_integration_client_secret(
    client_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IntegrationClientSecretOut:
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client_id)
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Integrační klient nebyl nalezen.")

    now = datetime.now(UTC)
    for secret in client.secrets:
        if secret.revoked_at is None:
            secret.revoked_at = now
            secret.rotated_at = now
            db.add(secret)

    plaintext = generate_integration_token(settings)
    token_record = build_token_record(plaintext)
    secret = models.IntegrationClientSecret(
        client_id=client.id,
        token_hash=token_record.token_hash,
        token_prefix=token_record.token_prefix,
        token_last4=token_record.token_last4,
        token_fingerprint=token_record.token_fingerprint,
    )
    db.add(secret)
    db.commit()
    db.refresh(client)
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client.id)
    ).scalar_one()
    return IntegrationClientSecretOut(client=_serialize_client(client), plaintext_token=plaintext)


@router.post("/clients/{client_id}/disable", response_model=IntegrationClientOut)
def disable_integration_client(
    client_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IntegrationClientOut:
    client = db.get(models.IntegrationClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Integrační klient nebyl nalezen.")
    client.status = models.IntegrationClientStatus.DISABLED.value
    db.add(client)
    db.commit()
    db.refresh(client)
    return _serialize_client(client)


@router.post("/clients/{client_id}/enable", response_model=IntegrationClientOut)
def enable_integration_client(
    client_id: int,
    payload: IntegrationClientUpdateIn | None = None,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IntegrationClientOut:
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client_id)
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Integrační klient nebyl nalezen.")
    client.status = models.IntegrationClientStatus.ACTIVE.value
    if payload is not None:
        if payload.scopes is not None:
            client.scopes = sorted(set(item.strip() for item in payload.scopes if item.strip()))
        if payload.allowed_employment_ids is not None:
            client.allowed_employment_ids = sorted(set(int(item) for item in payload.allowed_employment_ids))
        if payload.allowed_employee_ids is not None:
            client.allowed_employee_ids = sorted(set(int(item) for item in payload.allowed_employee_ids))
        if payload.ip_allowlist is not None:
            client.ip_allowlist = sorted(set(item.strip() for item in payload.ip_allowlist if item.strip()))
        if payload.expires_at is not None:
            client.expires_at = _parse_optional_datetime(payload.expires_at)
    db.add(client)
    db.commit()
    db.refresh(client)
    return _serialize_client(client)


@router.post("/clients/{client_id}/revoke-secret", response_model=IntegrationClientOut)
def revoke_integration_client_secret(
    client_id: int,
    _admin=Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IntegrationClientOut:
    client = db.execute(
        select(models.IntegrationClient)
        .options(selectinload(models.IntegrationClient.secrets))
        .where(models.IntegrationClient.id == client_id)
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Integrační klient nebyl nalezen.")
    now = datetime.now(UTC)
    for secret in client.secrets:
        if secret.revoked_at is None:
            secret.revoked_at = now
            db.add(secret)
    client.status = models.IntegrationClientStatus.REVOKED.value
    db.add(client)
    db.commit()
    db.refresh(client)
    return _serialize_client(client)
