"""Add attendance reminder events table.

Revision ID: 2026_03_13_0008
Revises: 2026_02_21_0007
Create Date: 2026-03-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_03_13_0008"
down_revision = "2026_02_21_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_reminder_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("reminder_type", sa.String(length=32), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("sent_to", sa.String(length=160), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instance_id",
            "attendance_date",
            "reminder_type",
            "sequence_no",
            name="uq_attendance_reminder_event_unique",
        ),
    )
    op.create_index(
        "ix_attendance_reminder_events_instance_date",
        "attendance_reminder_events",
        ["instance_id", "attendance_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_attendance_reminder_events_instance_date", table_name="attendance_reminder_events")
    op.drop_table("attendance_reminder_events")
