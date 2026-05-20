from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import (
    Attendance,
    AttendanceReminderEvent,
    Base,
    ClientType,
    Employment,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserRole,
    ShiftPlan,
)
from app.security.passwords import hash_password
from app.services.attendance_reminders import process_attendance_reminders


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        session_secret="x" * 32,
        csrf_secret="y" * 32,
    )


def _session_local() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return session_local


def _seed_user_with_employment(db: Session, *, instance_id: str, name: str, email: str) -> Employment:
    instance = Instance(
        id=instance_id,
        client_type=ClientType.WEB,
        device_fingerprint=f"fp-{instance_id}",
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
        instance_id=instance.id,
        is_active=True,
        password_hash=hash_password("StrongPass123").value,
    )
    employment = Employment(
        user=user,
        title="Výchozí úvazek",
        employment_type="DPP_DPC",
        start_date=date(2025, 1, 1),
        end_date=None,
        is_active=True,
    )
    db.add(instance)
    db.add(user)
    db.add(employment)
    db.commit()
    db.refresh(employment)
    return employment


def test_missing_arrival_reminder_is_sent_once_per_sequence() -> None:
    session_local = _session_local()

    with session_local() as db:
        employment = _seed_user_with_employment(db, instance_id="inst-1", name="Jana", email="jana@example.com")
        db.add(ShiftPlan(employment_id=employment.id, instance_id="inst-1", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time="16:00"))
        db.commit()

        sent: list[tuple[str, str]] = []

        count = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 8, 26),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert count == 3
        assert sent == [
            ("jana@example.com", "Nemas zapsany prichod"),
            ("jana@example.com", "Nemas zapsany prichod"),
            ("jana@example.com", "Nemas zapsany prichod"),
        ]

        count_again = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 8, 26),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert count_again == 0
        assert db.query(AttendanceReminderEvent).count() == 3


def test_missing_departure_reminder_starts_two_hours_after_planned_departure() -> None:
    session_local = _session_local()

    with session_local() as db:
        employment = _seed_user_with_employment(db, instance_id="inst-2", name="Marie", email="marie@example.com")
        db.add(ShiftPlan(employment_id=employment.id, instance_id="inst-2", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time="16:00"))
        db.add(Attendance(employment_id=employment.id, instance_id="inst-2", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time=None))
        db.commit()

        sent: list[tuple[str, str]] = []

        early_count = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 17, 59),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert early_count == 0

        count = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 18, 21),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert count == 3
        assert sent == [
            ("marie@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
            ("marie@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
            ("marie@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
        ]


def test_previous_day_missing_departure_reminder_runs_from_8am() -> None:
    session_local = _session_local()

    with session_local() as db:
        employment = _seed_user_with_employment(db, instance_id="inst-3", name="Eva", email="eva@example.com")
        db.add(Attendance(employment_id=employment.id, instance_id="inst-3", date=datetime(2026, 3, 12).date(), arrival_time="08:00", departure_time=None))
        db.commit()

        sent: list[tuple[str, str]] = []

        early_count = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 7, 59),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert early_count == 0

        count = process_attendance_reminders(
            db,
            _settings(),
            now=datetime(2026, 3, 13, 8, 31),
            send_email=lambda to_email, subject, body: sent.append((to_email, subject)),
        )
        assert count == 4
        assert sent == [
            ("eva@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
            ("eva@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
            ("eva@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
            ("eva@example.com", "Jsi jeste v praci? Nemas zapsan odchod"),
        ]
