from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    AppSettings,
    Attendance,
    AttendanceReminderEvent,
    PortalUser,
    PortalUserRole,
    ShiftPlan,
)
from app.security.crypto import decrypt_secret
from app.services.prague_time import combine_prague, combine_prague_hhmm, prague_now

logger = logging.getLogger(__name__)

ARRIVAL_REMINDER = "missing_arrival"
SAME_DAY_DEPARTURE_REMINDER = "missing_departure_after_shift"
PREVIOUS_DAY_DEPARTURE_REMINDER = "missing_departure_previous_day"
ARRIVAL_SUBJECT = "Nemáš zapsaný příchod"
DEPARTURE_SUBJECT = "Jsi ještě v práci? Nemáš zapsán odchod"

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
        raise ValueError("SMTP není nastaveno.")

    username = (cfg.smtp_username or "").strip()
    port = cfg.smtp_port
    if port is None:
        raise ValueError("SMTP port není nastaven.")
    smtp_secret = settings.smtp_password_secret or settings.session_secret
    decrypted_password = decrypt_secret(cfg.smtp_password, secret=smtp_secret) if cfg.smtp_password else None
    password = decrypted_password.strip() if decrypted_password else None
    security = (cfg.smtp_security or "SSL").strip().upper()
    from_email = (cfg.smtp_from_email or username or "").strip()
    if not from_email:
        raise ValueError("Chybí odesílací e-mail.")

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


def _already_sent_keys(db: Session, attendance_date: date) -> set[tuple[str, date, str, int]]:
    rows = db.execute(
        select(AttendanceReminderEvent).where(AttendanceReminderEvent.attendance_date == attendance_date)
    ).scalars().all()
    return {(row.instance_id, row.attendance_date, row.reminder_type, row.sequence_no) for row in rows}


def _record_sent(db: Session, instance_id: str, attendance_date: date, reminder_type: str, sequence_no: int, sent_to: str) -> None:
    db.add(
        AttendanceReminderEvent(
            instance_id=instance_id,
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

    users = db.execute(
        select(PortalUser).where(
            PortalUser.is_active.is_(True),
            PortalUser.role == PortalUserRole.EMPLOYEE,
            PortalUser.instance_id.is_not(None),
        )
    ).scalars().all()
    if not users:
        return 0

    instance_ids = [user.instance_id for user in users if user.instance_id]
    plans = db.execute(
        select(ShiftPlan).where(ShiftPlan.date.in_([today, yesterday]), ShiftPlan.instance_id.in_(instance_ids))
    ).scalars().all()
    attendances = db.execute(
        select(Attendance).where(Attendance.date.in_([today, yesterday]), Attendance.instance_id.in_(instance_ids))
    ).scalars().all()

    plan_by_key = {(plan.instance_id, plan.date): plan for plan in plans}
    attendance_by_key = {(row.instance_id, row.date): row for row in attendances}
    already_sent = _already_sent_keys(db, today) | _already_sent_keys(db, yesterday)
    sent_count = 0

    for user in users:
        if not user.instance_id:
            continue
        plan = plan_by_key.get((user.instance_id, today))
        attendance = attendance_by_key.get((user.instance_id, today))
        previous_day_attendance = attendance_by_key.get((user.instance_id, yesterday))

        if plan and plan.arrival_time and (attendance is None or attendance.arrival_time is None):
            first_at = combine_prague_hhmm(today, plan.arrival_time) + timedelta(minutes=5)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (user.instance_id, today, ARRIVAL_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    ARRIVAL_SUBJECT,
                    "Nemáš zapsaný příchod.\n\nProsím zkontroluj dnešní docházku.",
                )
                _record_sent(db, user.instance_id, today, ARRIVAL_REMINDER, sequence_no, user.email)
                already_sent.add(key)
                sent_count += 1

        if plan and plan.departure_time and attendance and attendance.arrival_time and not attendance.departure_time:
            first_at = combine_prague_hhmm(today, plan.departure_time) + timedelta(hours=2)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (user.instance_id, today, SAME_DAY_DEPARTURE_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    DEPARTURE_SUBJECT,
                    "Máš naplánované ukončení směny, ale stále nemáš zapsán odchod.\n\n"
                    "Jsi ještě v práci, nebo jsi jen zapomněl zapsat odchod? Prosím zkontroluj dnešní docházku.",
                )
                _record_sent(db, user.instance_id, today, SAME_DAY_DEPARTURE_REMINDER, sequence_no, user.email)
                already_sent.add(key)
                sent_count += 1

        if previous_day_attendance and previous_day_attendance.arrival_time and not previous_day_attendance.departure_time:
            first_at = combine_prague(today, 8, 0)
            due_attempts = _scheduled_attempt_count(current, first_at, interval_minutes=10, max_attempts=5)
            for sequence_no in range(1, due_attempts + 1):
                key = (user.instance_id, yesterday, PREVIOUS_DAY_DEPARTURE_REMINDER, sequence_no)
                if key in already_sent:
                    continue
                sender(
                    user.email,
                    DEPARTURE_SUBJECT,
                    "Včera máš zapsán příchod bez odchodu.\n\n"
                    "Nezapomněl(a) jsi dopsat včerejší odchod z práce? Prosím zkontroluj docházku za předchozí den.",
                )
                _record_sent(db, user.instance_id, yesterday, PREVIOUS_DAY_DEPARTURE_REMINDER, sequence_no, user.email)
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
