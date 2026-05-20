from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import Settings


def ensure_schema_up_to_date(settings: Settings) -> None:
    """Při startu dotáhne databázové migrace na aktuální head."""

    alembic_ini = Path(__file__).resolve().parent / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str((Path(__file__).resolve().parent / "migrations").as_posix()))

    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = settings.database_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
