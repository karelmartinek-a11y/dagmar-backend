from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import models

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

TOKEN_PREFIX_LEN = 16
TOKEN_FINGERPRINT_LEN = 12
TOKEN_HUMAN_PREFIX = "dgi_"


@dataclass(frozen=True)
class IntegrationTokenRecord:
    token_prefix: str
    token_hash: str
    token_last4: str
    token_fingerprint: str


@dataclass(frozen=True)
class IntegrationAuthResult:
    client: models.IntegrationClient
    secret: models.IntegrationClientSecret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_integration_token(settings: Settings) -> str:
    raw = secrets.token_bytes(max(16, settings.integration_token_length))
    return f"{TOKEN_HUMAN_PREFIX}{_b64url(raw)}"


def validate_integration_token_format(token: str) -> bool:
    if not token.startswith(TOKEN_HUMAN_PREFIX):
        return False
    if len(token) < len(TOKEN_HUMAN_PREFIX) + 16:
        return False
    if len(token) > 256:
        return False
    return True


def token_prefix(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:TOKEN_PREFIX_LEN]


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(("fingerprint:" + token).encode("utf-8")).hexdigest()[:TOKEN_FINGERPRINT_LEN]


def hash_token(token: str) -> str:
    return cast(str, _pwd_context.hash(token))


def verify_token(token: str, stored_hash: str) -> bool:
    try:
        return bool(_pwd_context.verify(token, stored_hash))
    except Exception:
        return False


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def build_token_record(token: str) -> IntegrationTokenRecord:
    return IntegrationTokenRecord(
        token_prefix=token_prefix(token),
        token_hash=hash_token(token),
        token_last4=token[-4:],
        token_fingerprint=token_fingerprint(token),
    )


def _client_is_active(client: models.IntegrationClient) -> bool:
    return client.status == models.IntegrationClientStatus.ACTIVE.value


def _client_is_expired(client: models.IntegrationClient) -> bool:
    expires_at = client.expires_at
    return expires_at is not None and expires_at <= datetime.now(UTC)


def _ip_allowed(client: models.IntegrationClient, source_ip: str | None) -> bool:
    allowlist = list(client.ip_allowlist or [])
    if not allowlist:
        return True
    if not source_ip:
        return False
    try:
        parsed_ip = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    for candidate in allowlist:
        try:
            if "/" in candidate:
                if parsed_ip in ipaddress.ip_network(candidate, strict=False):
                    return True
            elif parsed_ip == ipaddress.ip_address(candidate):
                return True
        except ValueError:
            continue
    return False


def verify_integration_token(
    db: Session,
    raw_token: str,
    *,
    source_ip: str | None = None,
) -> IntegrationAuthResult | None:
    if not validate_integration_token_format(raw_token):
        return None

    prefix = token_prefix(raw_token)
    candidates = db.execute(
        select(models.IntegrationClientSecret)
        .join(models.IntegrationClient)
        .where(models.IntegrationClientSecret.token_prefix == prefix)
        .where(models.IntegrationClientSecret.revoked_at.is_(None))
        .order_by(models.IntegrationClientSecret.id.desc())
    ).scalars().all()

    for secret in candidates:
        client = secret.client
        if not secret.token_hash or not verify_token(raw_token, secret.token_hash):
            continue
        if not _client_is_active(client):
            return None
        if _client_is_expired(client):
            return None
        if not _ip_allowed(client, source_ip):
            return None
        return IntegrationAuthResult(client=client, secret=secret)
    return None


def touch_client_last_used(db: Session, client: models.IntegrationClient) -> None:
    client.last_used_at = datetime.now(UTC)
    db.add(client)
    db.commit()
