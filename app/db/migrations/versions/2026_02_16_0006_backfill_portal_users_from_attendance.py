"""Backfill portal users for existing attendance instances.

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-16 00:00:00.000000
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", ".", ascii_only.lower()).strip(".")
    return re.sub(r"\.+", ".", cleaned)


def upgrade() -> None:
    bind = op.get_bind()

    existing_emails = {
        row[0]
        for row in bind.execute(sa.text("SELECT email FROM portal_users"))
        if row[0]
    }

    source_rows = bind.execute(
        sa.text(
            """
            SELECT i.id, i.display_name
            FROM instances i
            WHERE EXISTS (SELECT 1 FROM attendance a WHERE a.instance_id = i.id)
              AND NOT EXISTS (SELECT 1 FROM portal_users pu WHERE pu.instance_id = i.id)
            ORDER BY i.created_at ASC, i.id ASC
            """
        )
    ).all()

    for instance_id, display_name in source_rows:
        name = (display_name or "").strip() or f"Uživatel {str(instance_id)[:8]}"
        slug_base = _slugify(name) or "uzivatel"

        idx = 1
        candidate = f"{slug_base}@migrated.local"
        while candidate in existing_emails:
            idx += 1
            candidate = f"{slug_base}.{idx}@migrated.local"
        existing_emails.add(candidate)

        bind.execute(
            sa.text(
                """
                INSERT INTO portal_users (email, name, role, password_hash, is_active, instance_id, created_at, updated_at)
                VALUES (:email, :name, 'employee', NULL, true, :instance_id, NOW(), NOW())
                """
            ),
            {
                "email": candidate,
                "name": name,
                "instance_id": instance_id,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM portal_users WHERE email LIKE '%@migrated.local'"))
