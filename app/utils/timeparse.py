from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class TimeParseError(ValueError):
    pass


def is_valid_hhmm(value: str | None) -> bool:
    """Return True if value is None or a valid HH:MM time in 00:00-23:59."""
    if value is None:
        return True
    return _TIME_RE.match(value) is not None


def normalize_hhmm_or_none(value: str | None) -> str | None:
    """Normalize empty strings to None; validate HH:MM.

    Rules (DAGMAR):
    - allowed: "HH:MM" or null
    - empty string is treated as null
    - no other rules (arrival < departure etc.)
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if not is_valid_hhmm(value):
        raise TimeParseError("Invalid time format, expected HH:MM in 00:00-23:59")
    return value


def parse_hhmm_or_none(value: str | None) -> str | None:
    """Alias used by API layer; keeps return type Optional[str] with validation."""
    return normalize_hhmm_or_none(value)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_yyyy_mm_dd(value: str) -> date:
    if not isinstance(value, str) or _DATE_RE.match(value) is None:
        raise ValueError("Invalid date format, expected YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError("Invalid date") from e


@dataclass(frozen=True)
class YearMonth:
    year: int
    month: int

    @staticmethod
    def parse(year: str | int, month: str | int) -> YearMonth:
        try:
            y = int(year)
            m = int(month)
        except Exception as e:  # pragma: no cover
            raise ValueError("Invalid year/month") from e
        if y < 1970 or y > 2100:
            raise ValueError("Year out of range")
        if m < 1 or m > 12:
            raise ValueError("Month out of range")
        return YearMonth(year=y, month=m)


def days_in_month(year: int, month: int) -> int:
    # deterministic, no locale
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    this_month = date(year, month, 1)
    return (next_month - this_month).days
