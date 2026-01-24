"""add attendance lock table

Revision ID: 0001
Revises:
Create Date: 2026-01-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_locks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.String(length=36), sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("instance_id", "year", "month", name="uq_attendance_lock_instance_month"),
    )
    op.create_index(
        "ix_attendance_locks_instance_month",
        "attendance_locks",
        ["instance_id", "year", "month"],
    )


def downgrade() -> None:
    op.drop_index("ix_attendance_locks_instance_month", table_name="attendance_locks")
    op.drop_table("attendance_locks")
