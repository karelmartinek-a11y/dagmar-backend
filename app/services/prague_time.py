from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PRAGUE_TIMEZONE = ZoneInfo("Europe/Prague")


def prague_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(PRAGUE_TIMEZONE)
    if now.tzinfo is None:
        return now.replace(tzinfo=PRAGUE_TIMEZONE)
    return now.astimezone(PRAGUE_TIMEZONE)


def prague_today(now: datetime | None = None) -> date:
    return prague_now(now).date()


def prague_minutes_since_midnight(now: datetime | None = None) -> int:
    current = prague_now(now)
    return current.hour * 60 + current.minute


def prague_time_payload(now: datetime | None = None) -> dict[str, str]:
    current = prague_now(now)
    return {
        "datetime": current.isoformat(timespec="seconds"),
        "timezone": "Europe/Prague",
        "source": "server",
    }


def combine_prague(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=PRAGUE_TIMEZONE)


def combine_prague_hhmm(day: date, hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return combine_prague(day, int(hour), int(minute))
