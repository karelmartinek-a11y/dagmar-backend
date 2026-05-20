from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.db.models import (
    AppSettings,
    Attendance,
    AttendanceReminderEvent,
    Employment,
    PortalUser,
    PortalUserRole,
    ShiftPlan,
)
from app.security.crypto import decrypt_secret
from app.services.employment_access import employment_is_valid_on_day
from app.services.prague_time import combine_prague, combine_prague_hhmm, prague_now

logger = logging.getLogger(__name__)

ARRIVAL_REMINDER = "missing_arrival"
SAME_DAY_DEPARTURE_REMINDER = "missing_departure_after_shift"
PREVIOUS_DAY_DEPARTURE_REMINDER = "missing_departure_previous_day"
ARRIVAL_SUBJECT = "Nemas zapsany prichod"
DEPARTURE_SUBJECT = "Jsi jeste v praci? Nemas zapsan odchod"

ReminderSender = Callable[[str, str, str], None]
SCHEDULER_ADVISORY_LOCK = 248613


def _get_settings_row(db: Session) -> AppSettings:
    row = db.execute(select(AppSettings).where(AppSettings.id == 1)).scalars().first()
    if row is None:
        row = AppSettings(id=1, afternoon_cutoff_minutes=17 * 60)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _smtp_sender(settings: Settings, cfg: AppSettings) -> ReminderSender:
    host = (cfg.smtp_host or "").strip()
    if not host or not cfg.smtp_port:
        raise ValueError("SMTP neni nastaveno.")

    username = (cfg.smtp_username or "").strip()
    port = cfg.smtp_port
    if port is None:
        raise ValueError("SMTP port neni nastaven.")
    smtp_secret = settings.smtp_password_secret or settings.session_secret
    decrypted_password = decrypt_secret(cfg.smtp_password, secret=smtp_secret) if cfg.smtp_password else None
    password = decrypted_password.strip() if decrypted_password else None
    security = (cfg.smtp_security or "SSL").strip().upper()
    from_email = (cfg.smtp_from_email or username or "").strip()
    if not from_email:
        raise ValueError("Chybi odesilaci e-mail.")

    def send_email(to_email: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{cfg.smtp_from_name} <{from_email}>" if cfg.smtp_from_name else from_email
        msg["To"] = to_email
        msg.set_content(body)

        server: smtplib.SMTP
        if security == "SSL":
            server = smtplib.SMTP_SSL(host, int(port), timeout=20)
        else:
            server = smtplib.SMTP(host, int(port), timeout=20)
            if security == "STARTTLS":
                server.starttls()

        try:
            if username and password:
                server.login(username, password)
            server.send_message(msg)
        finally:
            server.quit()

    return send_email


def _scheduled_attempt_count(now: datetime, first_at: datetime, interval_minutes: int, max_attempts: int) -> int:
    if now < first_at:
        return 0
    elapsed_minutes = int((now - first_at).total_seconds() // 60)
    return min(max_attempts, (elapsed_minutes // interval_minutes) + 1)


def _already_sent_keys(db: Session, attendance_date: date) -> set[tuple[int, date, str, int]]:
    rows = db.execute(
        select(AttendanceReminderEvent).where(AttendanceReminderEvent.attendance_date == attendance_date)
    ).scalars().all()
    return {(row.employment_id, row.attendance_date, row.reminder_type, row.sequence_no) for row in rows}


def _record_sent(
    db: Session,
    employment: Employment,
    attendance_date: date,
    reminder_type: str,
    sequence_no: int,
    sent_to: str,
) -> None:
    db.add(
        AttendanceReminderEvent(
            employment_id=employment.id,
            instance_id=employment.user.instance_id if employment.user else None,
            attendance_date=attendance_date,
            reminder_type=reminder_type,
            sequence_no=sequence_no,
            sent_to=sent_to,
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()


def _try_advisory_lock(db: Session) -> bool:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return True
    return bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULER_ADVISORY_LOCK}).scalar())


def _release_advisory_lock(db: Session) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SCHEDULER_ADVISORY_LOCK})
    db.commit()


def process_attendance_reminders(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    send_email: ReminderSender | None = None,
) -> int:
    current = prague_now(now)
    today = current.date()
    yesterday = today - timedelta(days=1)
    cfg = _get_settings_row(db)
    sender = send_email or _smtp_sender(settings, cfg)

    users = (
        db.execute(
            select(PortalUser)
            .options(joinedload(PortalUser.employments))
            .where(
                PortalUser.is_active.is_(True),
                PortalUser.role == PortalUserRole.EMPLOYEE,
            )
        )
        .unique()
        .scalars()
        .all()
    )
    if not users:
        return 0

    eligible_employments = [
        employment
        for user in users
        for employment in user.employments
        if employment_is_valid_on_day(employment, today)
    ]
    if not eligible_employments:
        return 0

    employment_ids = [employment.id for employment in eligible_employments]
    plans = db.execute(
        select(ShiftPlan).where(ShiftPlan.date.in_([today, yesterday]), ShiftPlan.employment_id.in_(employment_ids))
    ).scalars().all()
    attendances = db.execute(
        select(Attendance).where(Attendance.date.in_([today, yesterday]), Attendance.employment_id.in_(employment_ids))
    ).scalars().all()

    plan_by_key = {(plan.employment_id, plan.date): plan for plan in plans}
    attendance_by_key = {(row.employment_id, row.date): row for row in attendances}
    already_sent = _already_sent_keys(db, today) | _already_sent_keys(db, yesterday)
    sent_count = 0

    for employment in eligible_employments:
        user = employment.user
        plan = plan_by_key.get((employment.id, today))
        attendance = attendance_by_key.get((employment.id, today))
        previous_day_attendance = attendance_by_key.get((employment.id, yesterday))

        if plan and plan.arrival_time and (attendance is None or attendance.arrival_time is None):
            first_at = combine_prague_hhmm(today, plan.arrival_time) + timedelta(minutes=5)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (employment.id, today, ARRIVAL_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    ARRIVAL_SUBJECT,
                    "Nemas zapsany prichod.\n\nProsim zkontroluj dnesni dochazku.",
                )
                _record_sent(db, employment, today, ARRIVAL_REMINDER, sequence_no, user.email)
                already_sent.add(key)
                sent_count += 1

        if plan and plan.departure_time and attendance and attendance.arrival_time and not attendance.departure_time:
            first_at = combine_prague_hhmm(today, plan.departure_time) + timedelta(hours=2)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (employment.id, today, SAME_DAY_DEPARTURE_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    DEPARTURE_SUBJECT,
                    "Mas naplanovane ukonceni smeny, ale stale nemas zapsan odchod.\n\n"
                    "Jsi jeste v praci, nebo jsi jen zapomnel zapsat odchod? Prosim zkontroluj dnesni dochazku.",
                )
                _record_sent(db, employment, today, SAME_DAY_DEPARTURE_REMINDER, sequence_no, user.email)
                already_sent.add(key)
                sent_count += 1

        if previous_day_attendance and previous_day_attendance.arrival_time and not previous_day_attendance.departure_time:
            first_at = combine_prague(today, 8, 0)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (employment.id, yesterday, PREVIOUS_DAY_DEPARTURE_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    DEPARTURE_SUBJECT,
                    "Vcera mas zapsan prichod bez odchodu.\n\n"
                    "Nezapomnel(a) jsi dopsat vcerejsi odchod z prace? Prosim zkontroluj dochazku za predchozi den.",
                )
                _record_sent(db, employment, yesterday, PREVIOUS_DAY_DEPARTURE_REMINDER, sequence_no, user.email)
                already_sent.add(key)
                sent_count += 1

    return sent_count


def run_attendance_reminders_once(settings: Settings, session_factory: Callable[[], Session], *, now: datetime | None = None) -> int:
    with session_factory() as db:
        try:
            if not _try_advisory_lock(db):
                return 0
            return process_attendance_reminders(db, settings, now=now)
        except Exception:
            logger.exception("Attendance reminder processing failed.")
            return 0
        finally:
            try:
                _release_advisory_lock(db)
            except Exception:
                logger.exception("Attendance reminder advisory lock release failed.")
