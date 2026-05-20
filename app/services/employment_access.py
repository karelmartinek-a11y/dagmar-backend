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


def _safe_start_date(employment: Employment) -> date | None:
    value = getattr(employment, "start_date", None)
    return value if isinstance(value, date) else None


def _safe_end_date(employment: Employment) -> date | None:
    value = getattr(employment, "end_date", None)
    return value if isinstance(value, date) else None


def employment_is_valid_on_day(employment: Employment, day: date) -> bool:
    start_date = _safe_start_date(employment)
    end_date = _safe_end_date(employment)
    if start_date is None:
        return False
    if not employment.is_active:
        return False
    if start_date > day:
        return False
    if end_date is not None and end_date < day:
        return False
    return True


def employment_is_within_login_window(employment: Employment, day: date) -> bool:
    start_date = _safe_start_date(employment)
    end_date = _safe_end_date(employment)
    if start_date is None:
        return False
    if not employment.is_active:
        return False
    allowed_from = add_calendar_months(start_date, -LOGIN_WINDOW_MONTHS)
    if day < allowed_from:
        return False
    if end_date is None:
        return True
    allowed_until = add_calendar_months(end_date, LOGIN_WINDOW_MONTHS)
    return day <= allowed_until


def employment_overlaps_month(employment: Employment, month_start: date, month_end: date) -> bool:
    start_date = _safe_start_date(employment)
    end_date = _safe_end_date(employment)
    if start_date is None:
        return False
    if not employment.is_active:
        return False
    if start_date >= month_end:
        return False
    if end_date is not None and end_date < month_start:
        return False
    return True


def employment_label(employment: Employment, user_name: str | None = None) -> str:
    resolved_user_name = user_name
    if resolved_user_name is None:
        employment_user = getattr(employment, "user", None)
        if employment_user is not None:
            resolved_user_name = getattr(employment_user, "name", None)
    base = (resolved_user_name or "").strip()
    raw_type = str(getattr(employment, "employment_type", "") or "").strip().upper()
    if raw_type == "HPP":
        type_label = "HPP"
    elif raw_type == "DPP_DPC":
        type_label = "DPP/DPČ"
    else:
        type_label = "Neurčený typ"
    title = str(getattr(employment, "title", "") or "").strip() or "Bez názvu úvazku"
    if base:
        return f"{base} - {type_label} - {title}"
    return f"{type_label} - {title}"


@dataclass(frozen=True)
class LoginEmploymentSelection:
    available: list[Employment]
    default: Employment | None


def select_login_employments(user: PortalUser, today: date) -> LoginEmploymentSelection:
    eligible = [employment for employment in user.employments if employment_is_within_login_window(employment, today)]
    eligible.sort(key=lambda item: (_safe_start_date(item) or date.max, item.id))

    current = [employment for employment in eligible if employment_is_valid_on_day(employment, today)]
    if current:
        current.sort(key=lambda item: (_safe_start_date(item) or date.max, item.id))
        return LoginEmploymentSelection(available=eligible, default=current[0])

    upcoming = [employment for employment in eligible if (_safe_start_date(employment) or date.min) > today]
    if upcoming:
        upcoming.sort(key=lambda item: (_safe_start_date(item) or date.max, item.id))
        return LoginEmploymentSelection(available=eligible, default=upcoming[0])

    recent = [employment for employment in eligible if (_safe_end_date(employment) or date.max) < today]
    recent.sort(key=lambda item: (_safe_end_date(item) or today, item.id), reverse=True)
    return LoginEmploymentSelection(available=eligible, default=recent[0] if recent else None)
