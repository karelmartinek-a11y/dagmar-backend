from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin, require_instance
from app.api.v1 import admin_users, attendance
from app.db.models import (
    Attendance,
    Base,
    ClientType,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserRole,
)
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
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
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


def test_admin_create_and_delete_user_cascades_attendance() -> None:
    client, session_local = _build_client()

    create_response = client.post(
        "/api/v1/admin/users",
        json={
            "name": "Jana",
            "email": "jana@example.com",
            "role": "employee",
            "employment_template": "HPP",
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["employment_template"] == "HPP"
    user_id = created["id"]

    with session_local() as db:
        user = db.get(PortalUser, user_id)
        assert user is not None
        assert user.instance_id is not None
        db.add(Attendance(instance_id=user.instance_id, date=datetime(2026, 3, 8, tzinfo=UTC).date(), arrival_time="08:00"))
        db.commit()
        instance_id = user.instance_id

    delete_response = client.delete(f"/api/v1/admin/users/{user_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}

    with session_local() as db:
        assert db.get(PortalUser, user_id) is None
        assert db.get(Instance, instance_id) is None
        remaining = db.execute(select(Attendance).where(Attendance.instance_id == instance_id)).scalars().all()
        assert remaining == []


def test_attendance_invalid_date_returns_400() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-2",
            client_type=ClientType.WEB,
            device_fingerprint="fp-2",
            status=InstanceStatus.ACTIVE,
            display_name="Marie",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
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
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
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


def test_attendance_future_or_locked_past_rules() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-2",
            client_type=ClientType.WEB,
            device_fingerprint="fp-2",
            status=InstanceStatus.ACTIVE,
            display_name="Marie",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        db.add(inst)
        db.add(Attendance(instance_id="inst-2", date=datetime(2026, 3, 8, tzinfo=UTC).date(), arrival_time="08:00", departure_time=None))
        db.commit()

    future_response = client.put(
        "/api/v1/attendance",
        json={"date": "2999-01-01", "arrival_time": "08:00", "departure_time": None},
    )
    assert future_response.status_code == 400

    change_past_response = client.put(
        "/api/v1/attendance",
        json={"date": "2026-03-08", "arrival_time": "09:00", "departure_time": None},
    )
    assert change_past_response.status_code == 400

    fill_missing_response = client.put(
        "/api/v1/attendance",
        json={"date": "2026-03-08", "arrival_time": "08:00", "departure_time": "16:00"},
    )
    assert fill_missing_response.status_code == 200
