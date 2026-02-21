from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin, require_instance
from app.api.v1 import admin_users, attendance
from app.db.models import Base, ClientType, Instance, InstanceStatus, PortalUser, PortalUserRole
from app.security.csrf import require_csrf


def _build_client() -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(admin_users.router)
    app.include_router(attendance.router)

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[admin_users.get_db] = override_db
    app.dependency_overrides[attendance.get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: {"ok": True}
    app.dependency_overrides[require_csrf] = lambda: None

    def override_instance() -> Instance:
        with TestingSessionLocal() as db:
            return db.get(Instance, "inst-2")

    app.dependency_overrides[require_instance] = override_instance

    return TestClient(app), TestingSessionLocal


def test_admin_update_user_smoke() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-1",
            client_type=ClientType.WEB,
            device_fingerprint="fp-1",
            status=InstanceStatus.ACTIVE,
            display_name="Pepa",
            created_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        user = PortalUser(
            email="old@example.com",
            name="Old Name",
            role=PortalUserRole.EMPLOYEE,
            instance_id=inst.id,
        )
        db.add(inst)
        db.add(user)
        db.commit()
        user_id = user.id

    response = client.put(
        f"/api/v1/admin/users/{user_id}",
        json={
            "name": "New Name",
            "email": " NEW@EXAMPLE.COM ",
            "phone": " +420123456789 ",
            "is_active": False,
            "profile_instance_id": "inst-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "new@example.com"
    assert payload["phone"] == "+420123456789"
    assert payload["profile_instance_id"] == "inst-1"
    assert payload["is_active"] is False


def test_attendance_invalid_date_returns_400() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-2",
            client_type=ClientType.WEB,
            device_fingerprint="fp-2",
            status=InstanceStatus.ACTIVE,
            display_name="Marie",
            created_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(inst)
        db.commit()

    response = client.put(
        "/api/v1/attendance",
        json={
            "date": "2026-99-99",
            "arrival_time": "08:00",
            "departure_time": "16:00",
        },
    )

    assert response.status_code == 400


def test_attendance_invalid_time_returns_400() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-2",
            client_type=ClientType.WEB,
            device_fingerprint="fp-2",
            status=InstanceStatus.ACTIVE,
            display_name="Marie",
            created_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(inst)
        db.commit()

    response = client.put(
        "/api/v1/attendance",
        json={
            "date": "2026-02-20",
            "arrival_time": "99:00",
            "departure_time": "16:00",
        },
    )

    assert response.status_code == 400
