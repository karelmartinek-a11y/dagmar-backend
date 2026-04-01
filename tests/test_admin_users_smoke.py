from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin, require_instance
from app.api.v1 import admin_users, attendance, portal_auth
from app.db.models import (
    Attendance,
    AttendanceLock,
    AuthLockoutState,
    Base,
    ClientType,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserRole,
)
from app.security.csrf import require_csrf
from app.security.passwords import hash_password


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
    app.include_router(portal_auth.router)

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[admin_users.get_db] = override_db
    app.dependency_overrides[attendance.get_db] = override_db
    app.dependency_overrides[portal_auth.get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: {"ok": True}
    app.dependency_overrides[require_csrf] = lambda: None

    def override_instance() -> Instance:
        with TestingSessionLocal() as db:
            return db.get(Instance, "inst-2")

    app.dependency_overrides[require_instance] = override_instance

    return TestClient(app), TestingSessionLocal


def test_portal_login_ignores_existing_lockout_and_uses_password_smoke() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-lock",
            client_type=ClientType.WEB,
            device_fingerprint="fp-lock",
            status=InstanceStatus.ACTIVE,
            display_name="Lock",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        password_hash = hash_password("StrongPass123").value
        user = PortalUser(
            email="lock@example.com",
            name="Lock User",
            role=PortalUserRole.EMPLOYEE,
            password_hash=password_hash,
            instance_id=inst.id,
        )
        db.add(inst)
        db.add(user)
        db.add(
            AuthLockoutState(
                actor_type="portal",
                principal="lock@example.com",
                failed_attempts=3,
                locked_until=(datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0),
            )
        )
        db.commit()

    wrong_password_response = client.post(
        "/api/v1/portal/login",
        json={"email": "lock@example.com", "password": "bad-password"},
    )
    assert wrong_password_response.status_code == 401

    with session_local() as db:
        lock_state = db.execute(
            select(AuthLockoutState).where(
                AuthLockoutState.actor_type == "portal",
                AuthLockoutState.principal == "lock@example.com",
            )
        ).scalars().first()
        assert lock_state is not None
        assert lock_state.locked_until is None

    login_response = client.post(
        "/api/v1/portal/login",
        json={"email": "lock@example.com", "password": "StrongPass123"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["instance_id"] == "inst-lock"


def test_portal_login_accepts_legacy_sha256_hash_and_rehashes_smoke() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-legacy",
            client_type=ClientType.WEB,
            device_fingerprint="fp-legacy",
            status=InstanceStatus.ACTIVE,
            display_name="Legacy",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        user = PortalUser(
            email="legacy@example.com",
            name="Legacy User",
            role=PortalUserRole.EMPLOYEE,
            password_hash=hashlib.sha256(b"StrongPass123").hexdigest(),
            instance_id=inst.id,
        )
        db.add(inst)
        db.add(user)
        db.commit()

    login_response = client.post(
        "/api/v1/portal/login",
        json={"email": "legacy@example.com", "password": "StrongPass123"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["instance_id"] == "inst-legacy"

    with session_local() as db:
        db_user = db.execute(select(PortalUser).where(PortalUser.email == "legacy@example.com")).scalars().one()
        assert db_user.password_hash is not None
        assert db_user.password_hash != hashlib.sha256(b"StrongPass123").hexdigest()
        assert db_user.password_hash.startswith("$argon2")


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


def test_admin_set_password_smoke() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-pass",
            client_type=ClientType.WEB,
            device_fingerprint="fp-pass",
            status=InstanceStatus.ACTIVE,
            display_name="Heslo",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            token_hash="legacy-token",
            token_issued_at=datetime.now(UTC),
        )
        user = PortalUser(
            email="pass@example.com",
            name="Pass",
            role=PortalUserRole.EMPLOYEE,
            instance_id=inst.id,
        )
        db.add(inst)
        db.add(user)
        db.commit()
        user_id = user.id

    response = client.post(
        f"/api/v1/admin/users/{user_id}/set-password",
        json={"password": "StrongPass123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_password"] is True

    with session_local() as db:
        db_user = db.get(PortalUser, user_id)
        assert db_user is not None
        assert db_user.password_hash
        db_inst = db.get(Instance, "inst-pass")
        assert db_inst is not None
        assert db_inst.token_hash is None


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


def test_admin_delete_user_with_shared_instance_removes_only_selected_user() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-shared",
            client_type=ClientType.WEB,
            device_fingerprint="fp-shared",
            status=InstanceStatus.ACTIVE,
            display_name="Sdilena",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        first_user = PortalUser(
            email="first@example.com",
            name="First",
            role=PortalUserRole.EMPLOYEE,
            instance_id=inst.id,
        )
        second_user = PortalUser(
            email="second@example.com",
            name="Second",
            role=PortalUserRole.EMPLOYEE,
            instance_id=inst.id,
        )
        db.add(inst)
        db.add(first_user)
        db.add(second_user)
        db.add(Attendance(instance_id=inst.id, date=datetime(2026, 3, 8, tzinfo=UTC).date(), arrival_time="08:00"))
        db.commit()
        first_user_id = first_user.id

    delete_response = client.delete(f"/api/v1/admin/users/{first_user_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}

    with session_local() as db:
        assert db.get(PortalUser, first_user_id) is None
        assert db.get(Instance, "inst-shared") is not None
        remaining = db.execute(select(Attendance).where(Attendance.instance_id == "inst-shared")).scalars().all()
        assert len(remaining) == 1


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


def test_attendance_ignores_month_lock_for_employee() -> None:
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
        db.add(
            AttendanceLock(
                instance_id="inst-2",
                year=2026,
                month=3,
                locked_by="admin",
            )
        )
        db.commit()

    month_response = client.get("/api/v1/attendance", params={"year": 2026, "month": 3})
    assert month_response.status_code == 200
    assert len(month_response.json()["days"]) == 31

    put_response = client.put(
        "/api/v1/attendance",
        json={"date": "2026-03-08", "arrival_time": "08:00", "departure_time": "16:00"},
    )
    assert put_response.status_code == 200


def test_admin_list_users_includes_lock_state() -> None:
    client, session_local = _build_client()

    with session_local() as db:
        inst = Instance(
            id="inst-locked-user",
            client_type=ClientType.WEB,
            device_fingerprint="fp-locked-user",
            status=InstanceStatus.ACTIVE,
            display_name="Locked User",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        user = PortalUser(
            email="locked.user@example.com",
            name="Locked User",
            role=PortalUserRole.EMPLOYEE,
            instance_id=inst.id,
        )
        lock_state = AuthLockoutState(
            actor_type="portal",
            principal="locked.user@example.com",
            failed_attempts=3,
            locked_until=(datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0),
        )
        db.add(inst)
        db.add(user)
        db.add(lock_state)
        db.commit()

    response = client.get("/api/v1/admin/users")

    assert response.status_code == 200
    payload = response.json()
    locked_user = next(item for item in payload["users"] if item["email"] == "locked.user@example.com")
    assert locked_user["is_locked"] is True
    assert locked_user["locked_until"] is not None
