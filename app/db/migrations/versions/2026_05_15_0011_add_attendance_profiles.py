"""Add attendance profiles with validity.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _build_label(name: str | None) -> str:
    normalized = (name or "").strip()
    if not normalized:
        return "DL Bez nazvu"
    if normalized.lower().startswith("dl "):
        return normalized
    return f"DL {normalized}"


def upgrade() -> None:
    op.create_table(
        "attendance_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.String(length=36), sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("instance_id", name="uq_attendance_profiles_instance_id"),
    )
    op.create_index("ix_attendance_profiles_instance_id", "attendance_profiles", ["instance_id"])

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT pu.instance_id, pu.name
            FROM portal_users pu
            WHERE pu.instance_id IS NOT NULL
            """
        )
    ).fetchall()

    known: set[str] = set()
    for instance_id, name in rows:
        if not instance_id or instance_id in known:
            continue
        known.add(instance_id)
        bind.execute(
            sa.text(
                """
                INSERT INTO attendance_profiles (instance_id, label, valid_from, valid_to, created_at, updated_at)
                VALUES (:instance_id, :label, NULL, NULL, NOW(), NOW())
                ON CONFLICT (instance_id) DO NOTHING
                """
            ),
            {"instance_id": instance_id, "label": _build_label(name)},
        )


def downgrade() -> None:
    op.drop_index("ix_attendance_profiles_instance_id", table_name="attendance_profiles")
    op.drop_table("attendance_profiles")
