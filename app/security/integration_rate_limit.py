from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from app.api.integration_common import IntegrationError

_WINDOW_SECONDS = 60
_buckets: dict[str, deque[float]] = {}


def _cleanup(bucket: deque[float], now: float) -> None:
    while bucket and now - bucket[0] >= _WINDOW_SECONDS:
        bucket.popleft()


def enforce_rate_limit(request: Request, *, namespace: str, limit_per_minute: int) -> None:
    if limit_per_minute <= 0:
        return
    client_key = getattr(request.state, "integration_rate_key", None)
    if not isinstance(client_key, str) or not client_key:
        source_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
        client_key = f"ip:{source_ip}"
    bucket_key = f"{namespace}:{client_key}"
    bucket = _buckets.setdefault(bucket_key, deque())
    now = time.monotonic()
    _cleanup(bucket, now)
    if len(bucket) >= limit_per_minute:
        raise IntegrationError(429, "rate_limited", "Byl překročen limit požadavků.")
    bucket.append(now)


def rate_limit_dependency(namespace: str, limit_per_minute: int) -> Callable[[Request], None]:
    def _dependency(request: Request) -> None:
        enforce_rate_limit(request, namespace=namespace, limit_per_minute=limit_per_minute)

    return _dependency

