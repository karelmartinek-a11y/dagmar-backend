"""Gunicorn configuration for DAGMAR backend.

Runs behind host-level Nginx at https://dagmar.hcasc.cz with proxy to:
  http://127.0.0.1:8101

This file is intended to be used by systemd unit:
  ExecStart=... gunicorn -c /opt/dagmar/backend/gunicorn.conf.py app.main:app

Notes:
- Keep bind on loopback only.
- Logging is written to stdout/stderr for journald; Nginx keeps its own logs.
"""

import multiprocessing
import os


def _int(env_name: str, default: int) -> int:
    try:
        return int(os.getenv(env_name, str(default)))
    except Exception:
        return default


# Bind only on loopback (per spec A.3)
bind = "127.0.0.1:8101"

# Worker model: UvicornWorker for ASGI (FastAPI)
worker_class = "uvicorn.workers.UvicornWorker"

# Deterministic-ish default workers; can be overridden via env.
# For small deployments, 2-4 workers is usually sufficient.
workers = _int("DAGMAR_GUNICORN_WORKERS", max(2, (multiprocessing.cpu_count() * 2) // 2))
threads = _int("DAGMAR_GUNICORN_THREADS", 1)

# Timeouts: keep sane values to avoid hanging workers.
timeout = _int("DAGMAR_GUNICORN_TIMEOUT", 60)
graceful_timeout = _int("DAGMAR_GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = _int("DAGMAR_GUNICORN_KEEPALIVE", 5)

# Logging: rely on systemd/journald (and optionally logrotate for exported files).
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("DAGMAR_LOG_LEVEL", "info")

# Avoid writing .pyc into /opt if permissions are strict
preload_app = False

# Security / request sizing: DAGMAR API is small (no uploads)
# Nginx should enforce its own limits; keep a minimal guard here.
limit_request_line = _int("DAGMAR_LIMIT_REQUEST_LINE", 8190)
limit_request_fields = _int("DAGMAR_LIMIT_REQUEST_FIELDS", 100)
limit_request_field_size = _int("DAGMAR_LIMIT_REQUEST_FIELD_SIZE", 8190)

# Ensure forwarded headers from Nginx are honored by Uvicorn
# (so URLs in responses/logs show correct scheme/host).
forwarded_allow_ips = os.getenv("DAGMAR_FORWARDED_ALLOW_IPS", "127.0.0.1")

# Uvicorn worker settings via env vars are supported; set defaults here.
# (Gunicorn passes them to worker).
raw_env = [
    "UVICORN_PROXY_HEADERS=1",
    "UVICORN_FORWARDED_ALLOW_IPS=" + forwarded_allow_ips,
]
