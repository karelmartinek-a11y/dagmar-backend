from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import Settings
from app.db.models import Base

# Alembic Config object
config = context.config

# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata is used by 'autogenerate'
target_metadata = Base.metadata


def _get_db_url() -> str:
    """Load DATABASE_URL deterministically.

    Priority:
      1) env var DATABASE_URL
      2) Settings().database_url (which reads from /etc/dagmar/backend.env via env)

    We do not import dotenv here; systemd provides the env file.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return Settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _get_db_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
