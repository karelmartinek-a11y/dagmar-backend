from __future__ import annotations

import hmac
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


def hash_password(password: str) -> PasswordHash:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    if len(password) > 512:
        raise ValueError("password too long")
    return PasswordHash(_pwd_context.hash(password))


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bool(_pwd_context.verify(password, password_hash))
    except Exception:
        return False


def constant_time_equals(a: str, b: str) -> bool:
    # Defensive helper; for password hashes passlib already does timing-safe checks.
    a_b = a.encode("utf-8")
    b_b = b.encode("utf-8")
    return hmac.compare_digest(a_b, b_b)
