from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import (
    Attendance,
    AttendanceReminderEvent,
    Base,
    ClientType,
    Instance,
    InstanceStatus,
    PortalUser,
    PortalUserRole,
    ShiftPlan,
)
from app.services.attendance_reminders import process_attendance_reminders


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        session_secret="x" * 32,
        csrf_secret="y" * 32,
    )


def _session_local() -> sessionmaker:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return session_local


def test_missing_arrival_reminder_is_sent_once_per_sequence() -> None:
    session_local = _session_local()

    with session_local() as db:
        db.add(
            Instance(
                id="inst-1",
                client_type=ClientType.WEB,
                device_fingerprint="fp-1",
                status=InstanceStatus.ACTIVE,
                display_name="Jana",
            )
        )
        db.add(
            PortalUser(
                email="jana@example.com",
                name="Jana",
                role=PortalUserRole.EMPLOYEE,
                instance_id="inst-1",
                is_active=True,
            )
        )
        db.add(ShiftPlan(instance_id="inst-1", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time="16:00"))
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
            ("jana@example.com", "Nemáš zapsaný příchod"),
            ("jana@example.com", "Nemáš zapsaný příchod"),
            ("jana@example.com", "Nemáš zapsaný příchod"),
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
        db.add(
            Instance(
                id="inst-2",
                client_type=ClientType.WEB,
                device_fingerprint="fp-2",
                status=InstanceStatus.ACTIVE,
                display_name="Marie",
            )
        )
        db.add(
            PortalUser(
                email="marie@example.com",
                name="Marie",
                role=PortalUserRole.EMPLOYEE,
                instance_id="inst-2",
                is_active=True,
            )
        )
        db.add(ShiftPlan(instance_id="inst-2", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time="16:00"))
        db.add(Attendance(instance_id="inst-2", date=datetime(2026, 3, 13).date(), arrival_time="08:00", departure_time=None))
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
            ("marie@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
            ("marie@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
            ("marie@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
        ]


def test_previous_day_missing_departure_reminder_runs_from_8am() -> None:
    session_local = _session_local()

    with session_local() as db:
        db.add(
            Instance(
                id="inst-3",
                client_type=ClientType.WEB,
                device_fingerprint="fp-3",
                status=InstanceStatus.ACTIVE,
                display_name="Eva",
            )
        )
        db.add(
            PortalUser(
                email="eva@example.com",
                name="Eva",
                role=PortalUserRole.EMPLOYEE,
                instance_id="inst-3",
                is_active=True,
            )
        )
        db.add(Attendance(instance_id="inst-3", date=datetime(2026, 3, 12).date(), arrival_time="08:00", departure_time=None))
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
            ("eva@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
            ("eva@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
            ("eva@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
            ("eva@example.com", "Jsi ještě v práci? Nemáš zapsán odchod"),
        ]
