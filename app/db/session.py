from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Singleton SQLAlchemy Engine.

    DAGMAR backend runs host-level and connects to Postgres published on loopback:
    - host: 127.0.0.1
    - port: 5433

    The DATABASE_URL must be provided via /etc/dagmar/backend.env.
    """

    global _engine
    cfg = get_settings()

    if _engine is None:
        # pool_pre_ping: avoid stale connections
        _engine = create_engine(
            cfg.database_url,
            pool_pre_ping=True,
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_timeout=cfg.db_pool_timeout_seconds,
        )
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def db_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a DB session and always closes."""

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """Alias used in API dependencies."""

    yield from db_session()


@contextmanager
def session_scope(database_url: str | None = None) -> Generator[Session, None, None]:
    """Context manager for scripts and one-off tasks."""

    engine = create_engine(database_url, pool_pre_ping=True) if database_url else get_engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
