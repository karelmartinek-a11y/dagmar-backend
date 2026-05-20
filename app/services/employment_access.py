from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from app.db.models import Employment, PortalUser

LOGIN_WINDOW_MONTHS = 1


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def employment_type_is_valid(value: str) -> bool:
    return value in {"HPP", "DPP_DPC"}


def employment_is_valid_on_day(employment: Employment, day: date) -> bool:
    if not employment.is_active:
        return False
    if employment.start_date > day:
        return False
    if employment.end_date is not None and employment.end_date < day:
        return False
    return True


def employment_is_within_login_window(employment: Employment, day: date) -> bool:
    if not employment.is_active:
        return False
    allowed_from = add_calendar_months(employment.start_date, -LOGIN_WINDOW_MONTHS)
    if day < allowed_from:
        return False
    if employment.end_date is None:
        return True
    allowed_until = add_calendar_months(employment.end_date, LOGIN_WINDOW_MONTHS)
    return day <= allowed_until


def employment_overlaps_month(employment: Employment, month_start: date, month_end: date) -> bool:
    if not employment.is_active:
        return False
    if employment.start_date >= month_end:
        return False
    if employment.end_date is not None and employment.end_date < month_start:
        return False
    return True


def employment_label(employment: Employment, user_name: str | None = None) -> str:
    resolved_user_name = user_name
    if resolved_user_name is None and employment.user is not None:
        resolved_user_name = employment.user.name
    base = (resolved_user_name or "").strip()
    type_label = "HPP" if employment.employment_type == "HPP" else "DPP/DPČ"
    title = employment.title.strip()
    if base:
        return f"{base} – {type_label} – {title}"
    return f"{type_label} – {title}"


@dataclass(frozen=True)
class LoginEmploymentSelection:
    available: list[Employment]
    default: Employment | None


def select_login_employments(user: PortalUser, today: date) -> LoginEmploymentSelection:
    eligible = [employment for employment in user.employments if employment_is_within_login_window(employment, today)]
    eligible.sort(key=lambda item: (item.start_date, item.id))

    current = [employment for employment in eligible if employment_is_valid_on_day(employment, today)]
    if current:
        current.sort(key=lambda item: (item.start_date, item.id))
        return LoginEmploymentSelection(available=eligible, default=current[0])

    upcoming = [employment for employment in eligible if employment.start_date > today]
    if upcoming:
        upcoming.sort(key=lambda item: (item.start_date, item.id))
        return LoginEmploymentSelection(available=eligible, default=upcoming[0])

    recent = [employment for employment in eligible if employment.end_date is not None and employment.end_date < today]
    recent.sort(key=lambda item: (item.end_date or today, item.id), reverse=True)
    return LoginEmploymentSelection(available=eligible, default=recent[0] if recent else None)
