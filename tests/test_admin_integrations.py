from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import ADMIN_IDENTITY_EMAIL, Settings, get_settings
from app.db.models import Base, Employment, IntegrationClient, PortalUser, PortalUserRole
from app.security.passwords import hash_password


def _build_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_url = f"sqlite:///{(tmp_path / 'admin-integrations.db').as_posix()}"
    get_settings.cache_clear()
    os.environ["DAGMAR_DATABASE_URL"] = database_url
    os.environ["DAGMAR_SESSION_SECRET"] = "x" * 32
    os.environ["DAGMAR_CSRF_SECRET"] = "y" * 32
    settings = Settings(
        database_url=database_url,
        session_secret="x" * 32,
        csrf_secret="y" * 32,
        admin_password_hash=hash_password("StrongPass123").value,
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
    return TestClient(app, base_url="https://testserver"), testing_session_local


def _csrf_headers(client: TestClient) -> dict[str, str]:
    csrf_response = client.get("/api/v1/admin/csrf")
    assert csrf_response.status_code == 200
    return {"X-CSRF-Token": csrf_response.json()["csrf_token"]}


def _login_admin(client: TestClient) -> dict[str, str]:
    headers = _csrf_headers(client)
    response = client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_IDENTITY_EMAIL, "password": "StrongPass123"},
        headers=headers,
    )
    assert response.status_code == 200
    return headers


def _seed_people(db: Session) -> tuple[PortalUser, PortalUser, Employment, Employment]:
    first_user = PortalUser(
        email="anna@example.cz",
        name="Anna Nováková",
        role=PortalUserRole.EMPLOYEE,
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    second_user = PortalUser(
        email="boris@example.cz",
        name="Boris Svoboda",
        role=PortalUserRole.EMPLOYEE,
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add_all([first_user, second_user])
    db.flush()

    active_employment = Employment(
        user_id=first_user.id,
        title="Recepce",
        employment_type="HPP",
        start_date=date(2026, 1, 1),
        end_date=None,
        is_active=True,
    )
    inactive_employment = Employment(
        user_id=second_user.id,
        title="Sezónní výpomoc",
        employment_type="DPP_DPC",
        start_date=date(2025, 6, 1),
        end_date=date(2026, 2, 28),
        is_active=False,
    )
    db.add_all([active_employment, inactive_employment])
    db.commit()
    return first_user, second_user, active_employment, inactive_employment


def test_admin_integration_options_and_create_flow(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        first_user, _, active_employment, _ = _seed_people(db)
        first_user_id = first_user.id
        active_employment_id = active_employment.id

    headers = _login_admin(client)
    options = client.get("/api/v1/admin/integrations/clients/options")
    assert options.status_code == 200
    payload = options.json()
    assert any(item["id"] == "integration:health" for item in payload["scopes"])
    assert any(item["id"] == "attendance:create" for item in payload["scopes"])
    assert any(item["id"] == "attendance:update" for item in payload["scopes"])
    assert any(item["id"] == "attendance:delete" for item in payload["scopes"])
    assert any(item["id"] == "SELECTED_EMPLOYMENTS" for item in payload["data_scope_modes"])

    create_response = client.post(
        "/api/v1/admin/integrations/clients",
        json={
            "name": "Mzdovy Import 1",
            "selected_scope_ids": ["integration:health", "attendance:read"],
            "data_scope_mode": "SELECTED_EMPLOYMENTS",
            "selected_employee_ids": [],
            "selected_employment_ids": [active_employment_id],
            "include_inactive_employments": False,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "DAYS_30",
            "custom_expiration_date": None,
        },
        headers=headers,
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert create_payload["plaintext_token"].startswith("dgi_")
    assert create_payload["client"]["configuration"]["selected_employment_ids"] == [active_employment.id]
    assert create_payload["client"]["configuration"]["selected_employee_ids"] == []
    assert create_payload["client"]["scope_summary"]
    assert create_payload["client"]["status"] == "ACTIVE"

    list_response = client.get("/api/v1/admin/integrations/clients")
    assert list_response.status_code == 200
    listed = list_response.json()[0]
    assert "plaintext_token" not in listed
    assert listed["data_scope_summary"]
    assert listed["scope_labels"]

    detail_response = client.get(f"/api/v1/admin/integrations/clients/{create_payload['client']['id']}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["configuration"]["selected_scope_ids"] == ["attendance:read", "integration:health"]
    assert detail_payload["configuration"]["selected_employment_ids"] == [active_employment.id]
    assert detail_payload["audit_summary"]["request_count"] == 0
    assert first_user_id not in detail_payload["configuration"]["selected_employee_ids"]


def test_admin_integration_rejects_unknown_scope_and_missing_csrf(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        _, _, active_employment, _ = _seed_people(db)
        active_employment_id = active_employment.id

    _login_admin(client)
    missing_csrf = client.post(
        "/api/v1/admin/integrations/clients",
        json={
            "name": "Bez CSRF",
            "selected_scope_ids": ["integration:health"],
            "data_scope_mode": "SELECTED_EMPLOYMENTS",
            "selected_employee_ids": [],
            "selected_employment_ids": [active_employment_id],
            "include_inactive_employments": False,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "NONE",
            "custom_expiration_date": None,
        },
        headers={"X-CSRF-Token": "neplatny-token"},
    )
    assert missing_csrf.status_code == 403

    headers = _csrf_headers(client)
    response = client.post(
        "/api/v1/admin/integrations/clients",
        json={
            "name": "Neznamy Scope",
            "selected_scope_ids": ["integration:health", "changes:read"],
            "data_scope_mode": "SELECTED_EMPLOYMENTS",
            "selected_employee_ids": [],
            "selected_employment_ids": [active_employment_id],
            "include_inactive_employments": False,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "NONE",
            "custom_expiration_date": None,
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "není v této verzi podporováno" in response.json()["detail"]


def test_admin_integration_rejects_invalid_name_and_unknown_employment(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        _seed_people(db)

    headers = _login_admin(client)
    invalid_name = client.post(
        "/api/v1/admin/integrations/clients",
        json={
            "name": "https://tajna.example",
            "selected_scope_ids": ["integration:health"],
            "data_scope_mode": "ALL_ACTIVE_EMPLOYMENTS",
            "selected_employee_ids": [],
            "selected_employment_ids": [],
            "include_inactive_employments": False,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "NONE",
            "custom_expiration_date": None,
        },
        headers=headers,
    )
    assert invalid_name.status_code == 400
    assert "písmena" in invalid_name.json()["detail"]

    missing_employment = client.post(
        "/api/v1/admin/integrations/clients",
        json={
            "name": "Validni Nazev",
            "selected_scope_ids": ["integration:health"],
            "data_scope_mode": "SELECTED_EMPLOYMENTS",
            "selected_employee_ids": [],
            "selected_employment_ids": [999999],
            "include_inactive_employments": False,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "NONE",
            "custom_expiration_date": None,
        },
        headers=headers,
    )
    assert missing_employment.status_code == 400
    assert "employment_id" in missing_employment.json()["detail"]


def test_admin_integration_update_rotate_disable_enable_and_revoke(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        first_user, second_user, active_employment, _ = _seed_people(db)
        first_user_id = first_user.id
        second_user_id = second_user.id
        integration_client = IntegrationClient(
            name="Puvodni Integrace",
            status="ACTIVE",
            scopes=["integration:health", "attendance:read"],
            data_scope_mode="SELECTED_EMPLOYMENTS",
            allowed_employment_ids=[active_employment.id],
            allowed_employee_ids=[],
            include_inactive_employments=False,
            ip_allowlist=[],
            created_by="pytest",
        )
        db.add(integration_client)
        db.commit()
        client_id = integration_client.id

    headers = _login_admin(client)
    update_response = client.put(
        f"/api/v1/admin/integrations/clients/{client_id}",
        json={
            "name": "Nove Nastaveni Integrace",
            "selected_scope_ids": ["integration:health", "employments:read"],
            "data_scope_mode": "SELECTED_EMPLOYEES",
                "selected_employee_ids": [first_user_id, second_user_id],
            "selected_employment_ids": [],
            "include_inactive_employments": True,
            "ip_restriction_mode": "NONE",
            "expiration_choice": "YEAR_1",
            "custom_expiration_date": None,
        },
        headers=headers,
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["name"] == "Nove Nastaveni Integrace"
    assert update_payload["configuration"]["selected_employee_ids"] == [first_user_id, second_user_id]
    assert update_payload["configuration"]["selected_employment_ids"] == []
    assert update_payload["configuration"]["expiration_choice"] == "YEAR_1"

    rotate_response = client.post(f"/api/v1/admin/integrations/clients/{client_id}/rotate", headers=headers)
    assert rotate_response.status_code == 200
    assert rotate_response.json()["plaintext_token"].startswith("dgi_")

    disable_response = client.post(f"/api/v1/admin/integrations/clients/{client_id}/disable", headers=headers)
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "DISABLED"

    enable_response = client.post(f"/api/v1/admin/integrations/clients/{client_id}/enable", headers=headers)
    assert enable_response.status_code == 200
    assert enable_response.json()["status"] == "ACTIVE"

    revoke_response = client.post(f"/api/v1/admin/integrations/clients/{client_id}/revoke-secret", headers=headers)
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "REVOKED"
