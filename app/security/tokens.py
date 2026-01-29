"""Instance Bearer token handling.

Requirements:
- Instance auth is Bearer token (random string).
- Token must be stored hashed in DB (never store plaintext).
- Backend must be able to validate tokens efficiently.

Implementation notes:
- We use a fixed prefix derived from the plaintext token (first 12 chars of SHA-256 hex)
  to find candidate row(s) quickly without storing plaintext.
- The full token is hashed using bcrypt.
- Token format: dg_<base64url>

This module is self-contained and does not depend on FastAPI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from passlib.context import CryptContext

from app.db import models

# Use Argon2 to avoid bcrypt length limits and wrap-bug detection.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


TOKEN_PREFIX_LEN = 12  # chars of sha256 hex
TOKEN_BYTES = 32
TOKEN_HUMAN_PREFIX = "dg_"


@dataclass(frozen=True)
class TokenRecord:
    """In-memory representation typically stored in DB."""

    token_prefix: str
    token_hash: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_instance_token() -> str:
    """Generate a new random token to be issued to an ACTIVE instance.

    Returned token is plaintext and must be shown only once to client.
    """

    raw = secrets.token_bytes(TOKEN_BYTES)
    return f"{TOKEN_HUMAN_PREFIX}{_b64url(raw)}"


def token_prefix(token: str) -> str:
    """Compute deterministic prefix for DB lookup."""

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest[:TOKEN_PREFIX_LEN]


def hash_token(token: str) -> str:
    """Hash plaintext token for storage."""

    # passlib will generate salt internally
    return cast(str, _pwd_context.hash(token))


def verify_token(token: str, stored_hash: str) -> bool:
    """Verify token against stored hash."""

    try:
        return bool(_pwd_context.verify(token, stored_hash))
    except Exception:
        return False


def constant_time_eq(a: str, b: str) -> bool:
    """Constant-time string compare."""

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def validate_token_format(token: str) -> bool:
    """Basic token format validation.

    We keep this intentionally permissive: just required prefix and sane length.
    """

    if not token.startswith(TOKEN_HUMAN_PREFIX):
        return False
    # Minimum length: prefix + some payload
    if len(token) < len(TOKEN_HUMAN_PREFIX) + 16:
        return False
    if len(token) > 256:
        return False
    return True


def make_token_record(token: str) -> TokenRecord:
    """Create DB-ready record (prefix + hash) from plaintext token."""

    return TokenRecord(token_prefix=token_prefix(token), token_hash=hash_token(token))


def maybe_redact(token: str | None) -> str:
    """Redact token for logs."""

    if not token:
        return "<none>"
    if len(token) <= 10:
        return "<redacted>"
    return token[:6] + "…" + token[-4:]


def verify_instance_token(db, raw_token: str) -> models.Instance | None:
    """Find ACTIVE instance matching provided Bearer token."""

    if not validate_token_format(raw_token):
        return None

    instances = cast(
        list[models.Instance],
        db.query(models.Instance)
        .filter(models.Instance.token_hash.isnot(None))
        .filter(models.Instance.token_hash != "")
        .all(),
    )

    for inst in instances:
        if inst.token_hash and verify_token(raw_token, inst.token_hash):
            return inst
    return None


def issue_instance_token_once(db, instance: models.Instance) -> str | None:
    """Issue a new token if none exists. Returns plaintext token or None if already issued."""

    if instance.token_hash:
        return None

    token = generate_instance_token()
    rec = make_token_record(token)

    instance.token_hash = rec.token_hash
    instance.token_issued_at = datetime.now(UTC)
    db.add(instance)
    return token


def rotate_instance_token(db, instance: models.Instance) -> str:
    """Issue a fresh token even if one already exists (rotates/invalidates previous token)."""
    token = generate_instance_token()
    rec = make_token_record(token)
    instance.token_hash = rec.token_hash
    instance.token_issued_at = datetime.now(UTC)
    db.add(instance)
    return token
