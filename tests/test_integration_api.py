import os
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    Attendance,
    AttendanceLock,
    Base,
    Employment,
    IntegrationAuditLog,
    IntegrationClient,
    IntegrationClientSecret,
    PortalUser,
    PortalUserRole,
    ShiftPlan,
)
from app.security.integration_tokens import build_token_record


def _build_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_url = f"sqlite:///{(tmp_path / 'integration-test.db').as_posix()}"
    get_settings.cache_clear()
    os.environ["DAGMAR_DATABASE_URL"] = database_url
    os.environ["DAGMAR_SESSION_SECRET"] = "x" * 32
    os.environ["DAGMAR_CSRF_SECRET"] = "y" * 32
    settings = Settings(
        database_url=database_url,
        session_secret="x" * 32,
        csrf_secret="y" * 32,
        rate_limit_enabled=False,
        disable_docs=True,
    )

    import app.db.session as db_session_module

    db_session_module._engine = None
    db_session_module._SessionLocal = None

    from app.main import create_app

    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return TestClient(app), testing_session_local


def _issue_token(
    db: Session,
    *,
    name: str,
    scopes: list[str],
    allowed_employment_ids: list[int],
    allowed_employee_ids: list[int],
) -> str:
    plaintext = f"dgi_{name}_token_1234567890"
    record = build_token_record(plaintext)
    client = IntegrationClient(
        name=name,
        status="ACTIVE",
        scopes=scopes,
        allowed_employment_ids=allowed_employment_ids,
        allowed_employee_ids=allowed_employee_ids,
        data_scope_mode="SELECTED_EMPLOYMENTS" if allowed_employment_ids else "ALL_EMPLOYMENTS",
        ip_allowlist=[],
        created_by="pytest",
    )
    db.add(client)
    db.flush()
    db.add(
        IntegrationClientSecret(
            client_id=client.id,
            token_hash=record.token_hash,
            token_prefix=record.token_prefix,
            token_last4=record.token_last4,
            token_fingerprint=record.token_fingerprint,
        )
    )
    db.commit()
    return plaintext


def _seed_domain_data(db: Session) -> dict[str, int]:
    primary_user = PortalUser(
        email="integration@example.cz",
        name="Integrační Test",
        role=PortalUserRole.EMPLOYEE,
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    secondary_user = PortalUser(
        email="outside@example.cz",
        name="Mimo Rozsah",
        role=PortalUserRole.EMPLOYEE,
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add_all([primary_user, secondary_user])
    db.flush()

    in_scope_employment = Employment(
        user_id=primary_user.id,
        title="Testovací úvazek",
        employment_type="DPP_DPC",
        start_date=date(2026, 5, 1),
        end_date=None,
        is_active=True,
    )
    out_of_scope_employment = Employment(
        user_id=secondary_user.id,
        title="Cizí úvazek",
        employment_type="HPP",
        start_date=date(2026, 5, 1),
        end_date=None,
        is_active=True,
    )
    db.add_all([in_scope_employment, out_of_scope_employment])
    db.flush()

    db.add(
        ShiftPlan(
            employment_id=in_scope_employment.id,
            date=date(2026, 6, 10),
            arrival_time="08:00",
            departure_time="16:00",
            status=None,
        )
    )
    db.add(
        Attendance(
            employment_id=in_scope_employment.id,
            date=date(2026, 6, 10),
            arrival_time="08:05",
            departure_time="16:10",
        )
    )
    db.add(
        Attendance(
            employment_id=in_scope_employment.id,
            date=date(2026, 6, 11),
            arrival_time="09:00",
            departure_time=None,
        )
    )
    db.add(
        AttendanceLock(
            employment_id=in_scope_employment.id,
            year=2026,
            month=5,
            locked_by="admin",
        )
    )
    db.commit()

    return {
        "employee_id": primary_user.id,
        "employment_id": in_scope_employment.id,
        "out_of_scope_employee_id": secondary_user.id,
        "out_of_scope_employment_id": out_of_scope_employment.id,
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_integration_health_requires_token(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    response = client.get("/api/v1/integration/health")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"


def test_integration_health_rejects_invalid_token(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    response = client.get("/api/v1/integration/health", headers={"Authorization": "Bearer dgi_INVALID"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_integration_read_endpoints_return_scoped_data_and_derived_punches(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="readonly",
            scopes=[
                "integration:health",
                "employments:read",
                "shift_plan:read",
                "attendance:read",
                "punches:read",
                "locks:read",
                "openapi:read",
            ],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    headers = _auth_headers(token)
    health = client.get("/api/v1/integration/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["ok"] is True

    employments = client.get("/api/v1/integration/employments", headers=headers)
    assert employments.status_code == 200
    assert employments.json()["data"][0]["display_label"]

    punches = client.get(
        "/api/v1/integration/punches?date_from=2026-06-10&date_to=2026-06-10",
        headers=headers,
    )
    assert punches.status_code == 200
    payload = punches.json()["data"]
    assert [row["event_type"] for row in payload] == ["ARRIVAL", "DEPARTURE"]
    assert all(row["source"] == "derived_from_attendance" for row in payload)
    assert all(row["raw_event_available"] is False for row in payload)


def test_integration_period_limit_is_enforced(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="period-limit",
            scopes=["integration:health", "attendance:read"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.get(
        "/api/v1/integration/attendances?date_from=2026-06-01&date_to=2026-07-15",
        headers=_auth_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "period_too_large"


def test_integration_openapi_contains_write_paths(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="openapi",
            scopes=["integration:health", "openapi:read"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.get(
        "/api/v1/integration/openapi.json",
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/integration/health" in paths
    assert "/api/v1/integration/attendances" in paths
    assert "/api/v1/integration/attendances/{attendance_id}" in paths


def test_missing_integration_route_uses_error_envelope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="missing-route",
            scopes=["integration:health"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.get(
        "/api/v1/integration/changes",
        headers=_auth_headers(token),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"]


def test_create_attendance_with_scope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="create-ok",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["employment_id"],
            "date": "2026-06-12",
            "arrival_time": "07:55",
            "departure_time": "16:05",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["employment_id"] == ids["employment_id"]
    assert payload["arrival_time"] == "07:55"
    assert payload["departure_time"] == "16:05"

    with session_local() as db:
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 12),
            )
        ).scalar_one()
        assert row.arrival_time == "07:55"
        assert row.departure_time == "16:05"


def test_create_attendance_rejects_missing_scope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="create-no-scope",
            scopes=["integration:health", "attendance:read"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={"employment_id": ids["employment_id"], "date": "2026-06-12", "arrival_time": "08:00"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_create_attendance_rejects_duplicate_employment_date(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="create-duplicate",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["employment_id"],
            "date": "2026-06-10",
            "arrival_time": "08:00",
            "departure_time": "16:00",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_attendance"


def test_patch_attendance_updates_arrival_time(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="update-arrival",
            scopes=["integration:health", "attendance:update"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 10),
            )
        ).scalar_one()
        attendance_id = row.id
        expected_updated_at = row.updated_at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    response = client.patch(
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={"arrival_time": "08:15", "expected_updated_at": expected_updated_at},
    )
    assert response.status_code == 200
    assert response.json()["arrival_time"] == "08:15"


def test_patch_attendance_updates_departure_time(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="update-departure",
            scopes=["integration:health", "attendance:update"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 11),
            )
        ).scalar_one()
        attendance_id = row.id

    response = client.patch(
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={"departure_time": "17:10"},
    )
    assert response.status_code == 200
    assert response.json()["departure_time"] == "17:10"


def test_patch_attendance_updates_all_safe_fields(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="update-both",
            scopes=["integration:health", "attendance:update"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 10),
            )
        ).scalar_one()
        attendance_id = row.id

    response = client.patch(
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={"arrival_time": "08:20", "departure_time": None},
    )
    assert response.status_code == 200
    assert response.json()["arrival_time"] == "08:20"
    assert response.json()["departure_time"] is None


def test_write_payload_rejects_unknown_field(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="unknown-field",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["employment_id"],
            "date": "2026-06-12",
            "arrival_time": "08:00",
            "unexpected": "value",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"invalid_request", "validation_error"}


def test_create_attendance_rejects_unknown_employment(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="missing-employment",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"], 999999],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={"employment_id": 999999, "date": "2026-06-12", "arrival_time": "08:00"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_create_attendance_rejects_out_of_scope_employment(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="out-of-scope",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["out_of_scope_employment_id"],
            "date": "2026-06-12",
            "arrival_time": "08:00",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_write_rejects_locked_period(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="locked",
            scopes=["integration:health", "attendance:create"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )

    response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["employment_id"],
            "date": "2026-05-20",
            "arrival_time": "08:00",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_locked"


def test_delete_attendance_with_scope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="delete-ok",
            scopes=["integration:health", "attendance:delete"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 11),
            )
        ).scalar_one()
        attendance_id = row.id

    response = client.request(
        "DELETE",
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    with session_local() as db:
        assert db.get(Attendance, attendance_id) is None


def test_delete_attendance_rejects_missing_scope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="delete-no-scope",
            scopes=["integration:health", "attendance:read"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 11),
            )
        ).scalar_one()
        attendance_id = row.id

    response = client.request(
        "DELETE",
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_update_conflict_when_expected_updated_at_is_stale(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="stale-update",
            scopes=["integration:health", "attendance:update"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        row = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 10),
            )
        ).scalar_one()
        attendance_id = row.id

    response = client.patch(
        f"/api/v1/integration/attendances/{attendance_id}",
        headers=_auth_headers(token),
        json={"arrival_time": "08:40", "expected_updated_at": "2026-06-01T00:00:00Z"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_write_audit_logs_create_update_delete_and_never_logs_plaintext_token(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        ids = _seed_domain_data(db)
        token = _issue_token(
            db,
            name="audit",
            scopes=["integration:health", "attendance:create", "attendance:update", "attendance:delete"],
            allowed_employment_ids=[ids["employment_id"]],
            allowed_employee_ids=[ids["employee_id"]],
        )
        existing = db.execute(
            select(Attendance).where(
                Attendance.employment_id == ids["employment_id"],
                Attendance.date == date(2026, 6, 10),
            )
        ).scalar_one()
        existing_attendance_id = existing.id

    create_response = client.post(
        "/api/v1/integration/attendances",
        headers=_auth_headers(token),
        json={
            "employment_id": ids["employment_id"],
            "date": "2026-06-13",
            "arrival_time": "08:10",
            "departure_time": "16:20",
        },
    )
    assert create_response.status_code == 201
    created_attendance_id = create_response.json()["attendance_id"]

    update_response = client.patch(
        f"/api/v1/integration/attendances/{existing_attendance_id}",
        headers=_auth_headers(token),
        json={"departure_time": "16:30"},
    )
    assert update_response.status_code == 200

    delete_response = client.request(
        "DELETE",
        f"/api/v1/integration/attendances/{created_attendance_id}",
        headers=_auth_headers(token),
        json={},
    )
    assert delete_response.status_code == 200

    with session_local() as db:
        logs = db.execute(
            select(IntegrationAuditLog)
            .where(IntegrationAuditLog.operation.in_(["attendance:create", "attendance:update", "attendance:delete"]))
            .order_by(IntegrationAuditLog.id.asc())
        ).scalars().all()
        assert [row.operation for row in logs] == ["attendance:create", "attendance:update", "attendance:delete"]
        assert logs[0].after_state == {
            "arrival_time": "08:10",
            "departure_time": "16:20",
            "last_changed_at": logs[0].after_state["last_changed_at"],
        }
        assert logs[1].before_state is not None
        assert logs[1].after_state is not None
        assert logs[2].before_state is not None
        assert logs[2].after_state == {"deleted": True}
        serialized = "\n".join(
            " | ".join(
                [
                    str(row.request_id),
                    str(row.path),
                    str(row.error_code),
                    str(row.before_state),
                    str(row.after_state),
                ]
            )
            for row in logs
        )
        assert token not in serialized
