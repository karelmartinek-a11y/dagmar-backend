from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin
from app.api.v1 import admin_attendance, admin_shift_plan, admin_users, attendance, portal_auth
from app.api.v1.admin_employments import router as admin_employments_router
from app.db.models import (
    Attendance,
    AttendanceLock,
    Base,
    ClientType,
    Employment,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserResetToken,
    PortalUserRole,
    ShiftPlan,
    ShiftPlanMonthInstance,
)
from app.security.csrf import require_csrf
from app.security.passwords import hash_password
from app.services.employment_access import add_calendar_months


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
    app.include_router(admin_employments_router)
    app.include_router(attendance.router)
    app.include_router(admin_attendance.router)
    app.include_router(admin_shift_plan.router)
    app.include_router(portal_auth.router)

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    for module in (admin_users, attendance, admin_attendance, admin_shift_plan, portal_auth):
        app.dependency_overrides[module.get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: {"username": "admin"}
    app.dependency_overrides[require_csrf] = lambda: None

    return TestClient(app), TestingSessionLocal


def _create_user(
    db: Session,
    *,
    email: str,
    is_active: bool = True,
    password: str = "StrongPass123",
    name: str = "Test User",
) -> PortalUser:
    instance = Instance(
        id=f"inst-{email}",
        client_type=ClientType.WEB,
        device_fingerprint=f"fp-{email}",
        status=InstanceStatus.ACTIVE,
        display_name=name,
        created_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
        employment_template="DPP_DPC",
    )
    user = PortalUser(
        email=email,
        name=name,
        role=PortalUserRole.EMPLOYEE,
        password_hash=hash_password(password).value,
        is_active=is_active,
        instance_id=instance.id,
    )
    db.add(instance)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_employment(
    db: Session,
    user: PortalUser,
    *,
    start_date: date,
    end_date: date | None = None,
    employment_type: str = "DPP_DPC",
    title: str = "Výchozí úvazek",
    is_active: bool = True,
) -> Employment:
    employment = Employment(
        user_id=user.id,
        title=title,
        employment_type=employment_type,
        start_date=start_date,
        end_date=end_date,
        is_active=is_active,
    )
    db.add(employment)
    db.commit()
    db.refresh(employment)
    return employment


def _portal_login(client: TestClient, email: str, password: str = "StrongPass123"):
    return client.post("/api/v1/portal/login", json={"email": email, "password": password})


def test_user_without_employment_cannot_login() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        _create_user(db, email="no-employment@example.com")

    response = _portal_login(client, "no-employment@example.com")
    assert response.status_code == 403


def test_user_with_open_ended_employment_can_login() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="employee@example.com")
        _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)

    response = _portal_login(client, "employee@example.com")
    assert response.status_code == 200
    payload = response.json()
    assert "instance_id" not in payload
    assert payload["employment_id"] is not None
    assert len(payload["available_employments"]) == 1


def test_manually_deactivated_user_with_valid_employment_cannot_login() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="deactivated@example.com", is_active=False)
        _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)

    response = _portal_login(client, "deactivated@example.com")
    assert response.status_code == 401


def test_portal_reset_rejects_inactive_user() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="inactive-reset@example.com")
        token_value = "inactive-reset-token"
        db.add(
            PortalUserResetToken(
                user_id=user.id,
                token_hash=hashlib.sha256(token_value.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        user.is_active = False
        db.add(user)
        db.commit()

    response = client.post("/api/v1/portal/reset", json={"token": token_value, "password": "NewStrongPass123"})
    assert response.status_code == 400


def test_employment_starting_in_less_than_one_calendar_month_can_login() -> None:
    client, session_local = _build_client()
    today = date.today()
    with session_local() as db:
        user = _create_user(db, email="soon@example.com")
        _add_employment(db, user, start_date=today + timedelta(days=20), end_date=None)

    response = _portal_login(client, "soon@example.com")
    assert response.status_code == 200


def test_employment_starting_in_more_than_one_calendar_month_cannot_login() -> None:
    client, session_local = _build_client()
    today = date.today()
    with session_local() as db:
        user = _create_user(db, email="later@example.com")
        _add_employment(db, user, start_date=add_calendar_months(today, 1) + timedelta(days=5), end_date=None)

    response = _portal_login(client, "later@example.com")
    assert response.status_code == 403


def test_employment_ended_less_than_one_calendar_month_ago_can_login() -> None:
    client, session_local = _build_client()
    today = date.today()
    with session_local() as db:
        user = _create_user(db, email="recent-ended@example.com")
        _add_employment(db, user, start_date=date(2025, 1, 1), end_date=today - timedelta(days=20))

    response = _portal_login(client, "recent-ended@example.com")
    assert response.status_code == 200


def test_employment_ended_more_than_one_calendar_month_ago_cannot_login() -> None:
    client, session_local = _build_client()
    today = date.today()
    with session_local() as db:
        user = _create_user(db, email="old-ended@example.com")
        _add_employment(db, user, start_date=date(2025, 1, 1), end_date=add_calendar_months(today, -1) - timedelta(days=5))

    response = _portal_login(client, "old-ended@example.com")
    assert response.status_code == 403


def test_employment_period_change_with_out_of_range_data_returns_409() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="range-conflict@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        db.add(Attendance(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 3, 10), arrival_time="08:00"))
        db.add(ShiftPlan(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 3, 12), arrival_time="08:00", departure_time="16:00"))
        db.commit()
        employment_id = employment.id

    response = client.put(
        f"/api/v1/admin/employments/{employment_id}",
        json={"start_date": "2026-03-11", "end_date": "2026-03-11"},
    )
    assert response.status_code == 409
    payload = response.json()["detail"]
    assert payload["attendance_count"] == 1
    assert payload["shift_plan_count"] == 1
    assert payload["requires_confirmation"] is True


def test_confirmed_employment_period_change_deletes_out_of_range_data() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="range-confirm@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        db.add(Attendance(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 3, 10), arrival_time="08:00"))
        db.add(ShiftPlan(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 3, 12), arrival_time="08:00", departure_time="16:00"))
        db.commit()
        employment_id = employment.id

    response = client.put(
        f"/api/v1/admin/employments/{employment_id}",
        json={
            "start_date": "2026-03-11",
            "end_date": "2026-03-11",
            "confirm_delete_out_of_range": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_attendance_count"] == 1
    assert payload["deleted_shift_plan_count"] == 1

    with session_local() as db:
        assert db.execute(select(Attendance).where(Attendance.employment_id == employment_id)).scalars().all() == []
        assert db.execute(select(ShiftPlan).where(ShiftPlan.employment_id == employment_id)).scalars().all() == []
        refreshed = db.get(Employment, employment_id)
        assert refreshed is not None
        assert refreshed.start_date == date(2026, 3, 11)
        assert refreshed.end_date == date(2026, 3, 11)


def test_attendance_and_shift_plan_are_stored_by_employment_id() -> None:
    client, session_local = _build_client()
    target_day = date.today() - timedelta(days=1)
    with session_local() as db:
        user = _create_user(db, email="storage@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        employment_id = employment.id
        instance_id = user.instance_id

    login_response = _portal_login(client, "storage@example.com")
    assert login_response.status_code == 200
    token = login_response.json()["instance_token"]

    attendance_response = client.put(
        "/api/v1/attendance",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "employment_id": employment_id,
                "date": target_day.isoformat(),
                "arrival_time": "08:00",
                "departure_time": "16:00",
        },
    )
    assert attendance_response.status_code == 200

    shift_plan_response = client.put(
        "/api/v1/admin/shift-plan",
        json={
            "employment_id": employment_id,
            "date": target_day.isoformat(),
            "arrival_time": "08:00",
            "departure_time": "16:00",
        },
    )
    assert shift_plan_response.status_code == 200

    with session_local() as db:
        attendance_row = db.execute(select(Attendance).where(Attendance.employment_id == employment_id)).scalars().one()
        shift_plan_row = db.execute(select(ShiftPlan).where(ShiftPlan.employment_id == employment_id)).scalars().one()
        assert attendance_row.employment_id == employment_id
        assert shift_plan_row.employment_id == employment_id
        assert attendance_row.instance_id == instance_id
        assert shift_plan_row.instance_id == instance_id


def test_portal_attendance_rejects_locked_month_for_read_and_write() -> None:
    client, session_local = _build_client()
    target_day = date(2026, 2, 10)
    with session_local() as db:
        user = _create_user(db, email="locked-month@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        db.add(
            AttendanceLock(
                employment_id=employment.id,
                instance_id=user.instance_id,
                year=target_day.year,
                month=target_day.month,
                locked_by="admin",
            )
        )
        db.commit()
        employment_id = employment.id

    login_response = _portal_login(client, "locked-month@example.com")
    assert login_response.status_code == 200
    token = login_response.json()["instance_token"]
    headers = {"Authorization": f"Bearer {token}"}

    read_response = client.get(
        f"/api/v1/attendance?employment_id={employment_id}&year={target_day.year}&month={target_day.month}",
        headers=headers,
    )
    assert read_response.status_code == 423

    write_response = client.put(
        "/api/v1/attendance",
        headers=headers,
        json={
            "employment_id": employment_id,
            "date": target_day.isoformat(),
            "arrival_time": "08:00",
            "departure_time": "16:00",
        },
    )
    assert write_response.status_code == 423


def test_shift_plan_defaults_to_active_employments_and_keeps_inactive_available_for_filtering() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        first_user = _create_user(db, email="plan-first@example.com", name="První Uživatel")
        second_user = _create_user(db, email="plan-second@example.com", name="Druhý Uživatel")
        inactive_user = _create_user(db, email="plan-inactive@example.com", name="Neaktivní Uživatel", is_active=False)
        first_employment = _add_employment(db, first_user, start_date=date(2025, 1, 1), title="Recepce")
        second_employment = _add_employment(db, second_user, start_date=date(2025, 1, 1), title="Úklid")
        inactive_employment = _add_employment(db, inactive_user, start_date=date(2025, 1, 1), title="Archiv")
        first_employment_id = first_employment.id
        second_employment_id = second_employment.id
        inactive_employment_id = inactive_employment.id
        db.add(
            ShiftPlan(
                employment_id=first_employment_id,
                instance_id=first_user.instance_id,
                date=date(2026, 5, 10),
                arrival_time="08:00",
                departure_time="16:00",
            )
        )
        db.commit()

    response = client.get("/api/v1/admin/shift-plan?year=2026&month=5")
    assert response.status_code == 200
    payload = response.json()

    assert payload["selected_employment_ids"] == [first_employment_id, second_employment_id]
    assert [row["employment_id"] for row in payload["rows"]] == [first_employment_id, second_employment_id]
    assert inactive_employment_id in [item["id"] for item in payload["available_employments"]]
    inactive_meta = next(item for item in payload["available_employments"] if item["id"] == inactive_employment_id)
    assert inactive_meta["is_active_in_month"] is False
    assert payload["rows"][0]["days"][9]["arrival_time"] == "08:00"
    assert payload["rows"][1]["days"][9]["arrival_time"] is None


def test_employment_delete_with_related_data_returns_409_until_confirmed() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="delete-employment@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        db.add(Attendance(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 2, 10), arrival_time="08:00"))
        db.add(ShiftPlan(employment_id=employment.id, instance_id=user.instance_id, date=date(2026, 2, 11), arrival_time="08:00", departure_time="16:00"))
        db.add(AttendanceLock(employment_id=employment.id, instance_id=user.instance_id, year=2026, month=2, locked_by="admin"))
        db.add(ShiftPlanMonthInstance(employment_id=employment.id, instance_id=user.instance_id, year=2026, month=2))
        db.commit()
        employment_id = employment.id

    response = client.delete(f"/api/v1/admin/employments/{employment_id}")
    assert response.status_code == 409
    payload = response.json()["detail"]
    assert payload["code"] == "employment_delete_conflict"
    assert payload["attendance_count"] == 1
    assert payload["shift_plan_count"] == 1
    assert payload["attendance_lock_count"] == 1
    assert payload["shift_plan_selection_count"] == 1

    confirmed_response = client.request(
        "DELETE",
        f"/api/v1/admin/employments/{employment_id}",
        json={"confirm_delete_related": True},
    )
    assert confirmed_response.status_code == 200
    confirmed_payload = confirmed_response.json()
    assert confirmed_payload["deleted_attendance_count"] == 1
    assert confirmed_payload["deleted_shift_plan_count"] == 1
    assert confirmed_payload["deleted_attendance_lock_count"] == 1
    assert confirmed_payload["deleted_shift_plan_selection_count"] == 1

    with session_local() as db:
        assert db.get(Employment, employment_id) is None
        assert db.execute(select(Attendance).where(Attendance.employment_id == employment_id)).scalars().all() == []
        assert db.execute(select(ShiftPlan).where(ShiftPlan.employment_id == employment_id)).scalars().all() == []


def test_delete_user_removes_user_and_employments() -> None:
    client, session_local = _build_client()
    with session_local() as db:
        user = _create_user(db, email="delete-user@example.com")
        employment = _add_employment(db, user, start_date=date(2025, 1, 1), end_date=None)
        user_id = user.id
        employment_id = employment.id

    response = client.delete(f"/api/v1/admin/users/{user_id}")
    assert response.status_code == 200
    assert response.json()["ok"] is True

    with session_local() as db:
        assert db.get(PortalUser, user_id) is None
        assert db.get(Employment, employment_id) is None
