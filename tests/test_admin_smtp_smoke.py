from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin
from app.api.v1 import admin_smtp
from app.db.models import AppSettings, Base


def test_admin_smtp_get_never_leaks_password() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        db.add(AppSettings(id=1, afternoon_cutoff_minutes=17 * 60, smtp_password="top-secret"))
        db.commit()

    app = FastAPI()
    app.include_router(admin_smtp.router)

    def override_db():
        db: Session = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[admin_smtp.get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: {"ok": True}

    client = TestClient(app)
    response = client.get("/api/v1/admin/smtp")
    assert response.status_code == 200
    payload = response.json()
    assert payload["password_set"] is True
    assert "password" not in payload
