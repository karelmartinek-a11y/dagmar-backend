from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import models

INTEGRATION_NAMESPACE = "/api/v1/integration"


class IntegrationError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class IntegrationAuditContext:
    started_at: float
    client_id: int | None = None
    error_code: str | None = None
    row_count: int | None = None


def ensure_request_id(request: Request) -> str:
    current = getattr(request.state, "request_id", None)
    if isinstance(current, str) and current:
        return current
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    return request_id


def integration_error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = ensure_request_id(request)
    audit = get_audit_context(request)
    audit.error_code = code
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers={"X-Request-ID": request_id},
    )


def raise_integration_error(status_code: int, code: str, message: str) -> None:
    raise IntegrationError(status_code=status_code, code=code, message=message)


def get_source_ip(request: Request) -> str | None:
    x_real = request.headers.get("x-real-ip")
    if x_real:
        return x_real.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        return first or None
    return request.client.host if request.client else None


def query_hash(request: Request) -> str | None:
    if not request.url.query:
        return None
    return hashlib.sha256(request.url.query.encode("utf-8")).hexdigest()


def get_audit_context(request: Request) -> IntegrationAuditContext:
    ctx = getattr(request.state, "integration_audit", None)
    if isinstance(ctx, IntegrationAuditContext):
        return ctx
    ctx = IntegrationAuditContext(started_at=time.perf_counter())
    request.state.integration_audit = ctx
    return ctx


def init_integration_request(request: Request) -> None:
    ensure_request_id(request)
    get_audit_context(request)


def finalize_integration_audit(
    db: Session,
    request: Request,
    *,
    status_code: int,
) -> None:
    ctx = get_audit_context(request)
    duration_ms = int((time.perf_counter() - ctx.started_at) * 1000)
    log_row = models.IntegrationAuditLog(
        client_id=ctx.client_id,
        request_id=ensure_request_id(request),
        method=request.method,
        path=request.url.path,
        query_hash=query_hash(request),
        source_ip=get_source_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        status_code=status_code,
        error_code=ctx.error_code,
        row_count=ctx.row_count,
        duration_ms=duration_ms,
    )
    db.add(log_row)
    db.commit()


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Neplatný cursor.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Neplatný cursor.")
    return value


def parse_iso_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise_integration_error(status.HTTP_400_BAD_REQUEST, "invalid_request", f"Pole {field_name} není platné datum.")
        raise AssertionError from exc


def utc_isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    as_utc = value.astimezone(UTC)
    return as_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

