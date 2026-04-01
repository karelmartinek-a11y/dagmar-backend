from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from passlib.context import CryptContext

# Prefer Argon2 (modern, memory-hard). Fallback to bcrypt if needed.
_pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
)


@dataclass(frozen=True)
class PasswordHash:
    value: str


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool = False


_LEGACY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_password(password: str) -> PasswordHash:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    if len(password) > 512:
        raise ValueError("password too long")
    return PasswordHash(_pwd_context.hash(password))


def _is_legacy_sha256_hash(password_hash: str) -> bool:
    return bool(_LEGACY_SHA256_RE.fullmatch(password_hash))


def verify_password_details(password: str, password_hash: str) -> PasswordVerification:
    if not password_hash:
        return PasswordVerification(valid=False)
    if _is_legacy_sha256_hash(password_hash):
        computed_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return PasswordVerification(
            valid=constant_time_equals(computed_hash, password_hash),
            needs_rehash=True,
        )
    try:
        return PasswordVerification(
            valid=bool(_pwd_context.verify(password, password_hash)),
            needs_rehash=bool(_pwd_context.needs_update(password_hash)),
        )
    except Exception:
        return PasswordVerification(valid=False)


def verify_password(password: str, password_hash: str) -> bool:
    return verify_password_details(password, password_hash).valid


def is_password_hash_outdated(password_hash: str) -> bool:
    if not password_hash:
        return False
    if _is_legacy_sha256_hash(password_hash):
        return True
    try:
        return bool(_pwd_context.needs_update(password_hash))
    except Exception:
        return False


def constant_time_equals(a: str, b: str) -> bool:
    # Defensive helper; for password hashes passlib already does timing-safe checks.
    a_b = a.encode("utf-8")
    b_b = b.encode("utf-8")
    return hmac.compare_digest(a_b, b_b)
