from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    Attendance,
    Base,
    Employment,
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


def _seed_domain_data(db: Session) -> str:
    user = PortalUser(
        email="integration@example.cz",
        name="Integrační Test",
        role=PortalUserRole.EMPLOYEE,
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()

    employment = Employment(
        user_id=user.id,
        title="Testovací úvazek",
        employment_type="DPP_DPC",
        start_date=date(2026, 6, 1),
        end_date=None,
        is_active=True,
    )
    db.add(employment)
    db.flush()

    db.add(
        ShiftPlan(
            employment_id=employment.id,
            date=date(2026, 6, 10),
            arrival_time="08:00",
            departure_time="16:00",
            status=None,
        )
    )
    db.add(
        Attendance(
            employment_id=employment.id,
            date=date(2026, 6, 10),
            arrival_time="08:05",
            departure_time="16:10",
        )
    )

    plaintext = "dgi_test_token_1234567890"
    record = build_token_record(plaintext)
    client = IntegrationClient(
        name="test-client",
        status="ACTIVE",
        scopes=["integration:health", "employments:read", "shift_plan:read", "attendance:read", "punches:read", "locks:read", "openapi:read"],
        allowed_employment_ids=[employment.id],
        allowed_employee_ids=[user.id],
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


def test_integration_endpoints_return_scoped_data_and_derived_punches(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        token = _seed_domain_data(db)

    headers = {"Authorization": f"Bearer {token}"}

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
        token = _seed_domain_data(db)

    response = client.get(
        "/api/v1/integration/attendances?date_from=2026-06-01&date_to=2026-07-15",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "period_too_large"


def test_integration_openapi_contains_integration_paths(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        token = _seed_domain_data(db)

    response = client.get(
        "/api/v1/integration/openapi.json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/integration/health" in paths
    assert "/api/v1/integration/employments" in paths


def test_missing_integration_route_uses_error_envelope(tmp_path: Path) -> None:
    client, session_local = _build_client(tmp_path)
    with session_local() as db:
        token = _seed_domain_data(db)

    response = client.get(
        "/api/v1/integration/changes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
