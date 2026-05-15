from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AttendanceProfile


def get_profile_by_instance_id(db: Session, instance_id: str) -> AttendanceProfile | None:
    return db.execute(
        select(AttendanceProfile).where(AttendanceProfile.instance_id == instance_id)
    ).scalar_one_or_none()


def is_date_within_profile_validity(profile: AttendanceProfile | None, day: date) -> bool:
    if profile is None:
        return True
    if profile.valid_from and day < profile.valid_from:
        return False
    if profile.valid_to and day > profile.valid_to:
        return False
    return True
