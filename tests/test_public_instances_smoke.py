from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import public_instances
from app.db.models import AppSettings, Base, ClientType, Instance, InstanceStatus


def _build_client() -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(public_instances.router)

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[public_instances.get_db] = override_db
    return TestClient(app), TestingSessionLocal


def test_public_instance_lifecycle_smoke() -> None:
    client, session_local = _build_client()

    register = client.post(
        "/api/v1/instances/register",
        json={"client_type": "ANDROID", "device_fingerprint": "abc", "device_info": {"v": 1}},
    )
    assert register.status_code == 200
    instance_id = register.json()["instance_id"]

    pending_status = client.get(f"/api/v1/instances/{instance_id}/status")
    assert pending_status.status_code == 200
    assert pending_status.json()["status"] == "PENDING"

    with session_local() as db:
        inst = db.get(Instance, instance_id)
        assert inst is not None
        inst.status = InstanceStatus.ACTIVE
        inst.display_name = "Test"
        inst.employment_template = "DPP_DPC"
        inst.activated_at = datetime.now(timezone.utc)
        db.add(inst)
        db.add(AppSettings(id=1, afternoon_cutoff_minutes=17 * 60))
        db.commit()

    active_status = client.get(f"/api/v1/instances/{instance_id}/status")
    assert active_status.status_code == 200
    assert active_status.json()["status"] == "ACTIVE"

    claim = client.post(f"/api/v1/instances/{instance_id}/claim-token")
    assert claim.status_code == 200
    assert claim.json()["instance_token"].startswith("dg_")
