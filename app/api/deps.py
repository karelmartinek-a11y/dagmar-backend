from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.security.sessions import get_admin_session
from app.security.tokens import verify_instance_token


@dataclass(frozen=True)
class InstanceAuth:
    instance: models.Instance


def _bearer_from_auth_header(authorization: Optional[str]) -> Optional[str]:
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
    """Require a valid admin session.

    Session cookie is validated by app.security.sessions.
    """
    sess = get_admin_session(request)
    if not sess or not sess.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return sess


def require_instance_auth(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> InstanceAuth:
    """Require instance Bearer token.

    Token is issued after admin activation, stored hashed in DB.
    """
    token = _bearer_from_auth_header(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    instance = verify_instance_token(db=db, raw_token=token)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if instance.status != models.InstanceStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Instance not active")

    # last_seen update is done in endpoints that are polled frequently (status/attendance)
    return InstanceAuth(instance=instance)


def require_instance(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> models.Instance:
    """Backward-compatible alias returning the Instance directly."""
    return require_instance_auth(request=request, db=db, authorization=authorization).instance


def require_instance_by_id(
    instance_id: str,
    db: Session = Depends(get_db),
) -> models.Instance:
    inst = db.query(models.Instance).filter(models.Instance.id == instance_id).one_or_none()
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found")
    return inst
