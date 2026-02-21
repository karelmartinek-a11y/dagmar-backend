from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_ENC_PREFIX = "enc:v1:"


def _fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str, *, secret: str) -> str:
    token = _fernet_from_secret(secret).encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str | None, *, secret: str) -> str | None:
    if not value:
        return None
    if not value.startswith(_ENC_PREFIX):
        return value
    token = value[len(_ENC_PREFIX) :]
    try:
        return _fernet_from_secret(secret).decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Neplatný šifrovaný secret.") from exc
