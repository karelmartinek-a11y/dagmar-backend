from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("DAGMAR_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("DAGMAR_SESSION_SECRET", "x" * 32)
os.environ.setdefault("DAGMAR_CSRF_SECRET", "y" * 32)

from app.main import app


def test_health_v1_smoke() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
