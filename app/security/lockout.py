from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AuthLockoutState, AuthUnlockToken

LOCKOUT_THRESHOLD = 3
LOCKOUT_WINDOW = timedelta(hours=1)
PORTAL_LOCKOUT_DURATION = timedelta(hours=1)
ADMIN_LOCKOUT_DURATION = timedelta(days=36500)
UNLOCK_TOKEN_TTL = timedelta(hours=24)


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_lockout_state(db: Session, *, actor_type: str, principal: str, create: bool = True) -> AuthLockoutState | None:
    states = (
        db.execute(
            select(AuthLockoutState)
            .where(AuthLockoutState.actor_type == actor_type)
            .where(AuthLockoutState.principal == principal)
            .order_by(AuthLockoutState.id.asc())
        )
        .scalars()
        .all()
    )
    state = states[0] if states else None
    if state is None and create:
        state = AuthLockoutState(actor_type=actor_type, principal=principal)
        db.add(state)
        db.flush()
        return state
    if state is not None and len(states) > 1:
        duplicates = states[1:]
        state.failed_attempts = max(int(item.failed_attempts or 0) for item in states)
        first_failed_values: list[datetime] = []
        last_failed_values: list[datetime] = []
        locked_until_values: list[datetime] = []
        forgot_values: list[datetime] = []
        for item in states:
            first_failed_at = as_utc(item.first_failed_at)
            if first_failed_at is not None:
                first_failed_values.append(first_failed_at)
            last_failed_at = as_utc(item.last_failed_at)
            if last_failed_at is not None:
                last_failed_values.append(last_failed_at)
            locked_until = as_utc(item.locked_until)
            if locked_until is not None:
                locked_until_values.append(locked_until)
            last_forgot_sent_at = as_utc(item.last_forgot_sent_at)
            if last_forgot_sent_at is not None:
                forgot_values.append(last_forgot_sent_at)
        state.first_failed_at = min(first_failed_values) if first_failed_values else None
        state.last_failed_at = max(last_failed_values) if last_failed_values else None
        state.locked_until = max(locked_until_values) if locked_until_values else None
        state.last_forgot_sent_at = max(forgot_values) if forgot_values else None
        db.add(state)
        for duplicate in duplicates:
            db.delete(duplicate)
    return state


def is_locked(state: AuthLockoutState | None, now: datetime | None = None) -> bool:
    now = now or utc_now()
    if state is None or state.locked_until is None:
        return False
    return (as_utc(state.locked_until) or now) > now


def reset_lock_state(state: AuthLockoutState | None) -> None:
    if state is None:
        return
    state.failed_attempts = 0
    state.first_failed_at = None
    state.last_failed_at = None
    state.locked_until = None


def record_failed_login(
    state: AuthLockoutState,
    *,
    now: datetime | None = None,
    lock_duration: timedelta = PORTAL_LOCKOUT_DURATION,
) -> bool:
    now = now or utc_now()
    first_failed_at = as_utc(state.first_failed_at)
    window_reset = first_failed_at is None or now - first_failed_at > LOCKOUT_WINDOW
    was_locked = is_locked(state, now)
    if window_reset:
        state.failed_attempts = 0
        state.first_failed_at = now
    state.failed_attempts = int(state.failed_attempts or 0) + 1
    state.last_failed_at = now
    if state.failed_attempts >= LOCKOUT_THRESHOLD:
        state.locked_until = now + lock_duration
    return (not was_locked) and is_locked(state, now)


def issue_unlock_token(db: Session, *, actor_type: str, principal: str, purpose: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    db.add(
        AuthUnlockToken(
            actor_type=actor_type,
            principal=principal,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=now + UNLOCK_TOKEN_TTL,
        )
    )
    return token


def consume_unlock_token(db: Session, *, token: str, actor_type: str, purpose: str) -> AuthUnlockToken | None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    row = (
        db.execute(
            select(AuthUnlockToken)
            .where(AuthUnlockToken.token_hash == token_hash)
            .where(AuthUnlockToken.actor_type == actor_type)
            .where(AuthUnlockToken.purpose == purpose)
            .where(AuthUnlockToken.used_at.is_(None))
            .where(AuthUnlockToken.expires_at > now)
        )
        .scalars()
        .first()
    )
    if row is not None:
        row.used_at = now
        db.add(row)
    return row


def clear_user_lockout(db: Session, *, actor_type: str, principal: str) -> None:
    rows = db.execute(
        select(AuthLockoutState).where(
            AuthLockoutState.actor_type == actor_type,
            AuthLockoutState.principal == principal,
        )
    ).scalars().all()
    for row in rows:
        reset_lock_state(row)
        db.add(row)


def revoke_unlock_tokens(db: Session, *, actor_type: str, principal: str, purpose: str | None = None) -> None:
    statement = delete(AuthUnlockToken).where(
        AuthUnlockToken.actor_type == actor_type,
        AuthUnlockToken.principal == principal,
    )
    if purpose is not None:
        statement = statement.where(AuthUnlockToken.purpose == purpose)
    db.execute(statement)
