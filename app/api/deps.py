# ruff: noqa: B008
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.integration_common import (
    IntegrationError,
    get_audit_context,
    get_source_ip,
    init_integration_request,
)
from app.db import models
from app.db.session import get_db
from app.security.integration_tokens import (
    IntegrationTokenError,
    touch_client_last_used,
    verify_integration_token,
)
from app.security.sessions import get_admin_session
from app.security.tokens import verify_instance_token


@dataclass(frozen=True)
class InstanceAuth:
    instance: models.Instance


@dataclass(frozen=True)
class PortalUserAuth:
    instance: models.Instance
    user: models.PortalUser


@dataclass(frozen=True)
class IntegrationAuth:
    client: models.IntegrationClient
    secret: models.IntegrationClientSecret


def _bearer_from_auth_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0].strip(), parts[1].strip()
    if scheme.lower() != "bearer":
        return None
    return token or None


def require_admin(request: Request):
    sess = get_admin_session(request)
    if not sess or not sess.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return sess


def require_instance_auth(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> InstanceAuth:
    token = _bearer_from_auth_header(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    instance = verify_instance_token(db=db, raw_token=token)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if instance.status != models.InstanceStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Instance not active")

    return InstanceAuth(instance=instance)


def require_instance(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> models.Instance:
    return require_instance_auth(request=request, db=db, authorization=authorization).instance


def require_portal_user_auth(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> PortalUserAuth:
    auth = require_instance_auth(request=request, db=db, authorization=authorization)
    user = (
        db.execute(select(models.PortalUser).where(models.PortalUser.instance_id == auth.instance.id))
        .scalars()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="K tokenu neni prirazen uzivatel")
    return PortalUserAuth(instance=auth.instance, user=user)


def require_integration_auth(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> IntegrationAuth:
    init_integration_request(request)
    token = _bearer_from_auth_header(authorization)
    if not token:
        raise IntegrationError(
            status.HTTP_401_UNAUTHORIZED,
            "missing_token",
            "Chybí přístupový token.",
        )

    try:
        auth = verify_integration_token(db, token, source_ip=get_source_ip(request))
    except IntegrationTokenError as exc:
        if exc.code == "ip_forbidden":
            raise IntegrationError(status.HTTP_403_FORBIDDEN, exc.code, exc.message) from exc
        raise IntegrationError(status.HTTP_403_FORBIDDEN, "client_disabled", exc.message) from exc

    if auth is None:
        raise IntegrationError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_token",
            "Přístupový token není platný.",
        )

    audit = get_audit_context(request)
    audit.client_id = auth.client.id

    try:
        touch_client_last_used(db, auth.client)
    except Exception:
        db.rollback()

    return IntegrationAuth(client=auth.client, secret=auth.secret)
