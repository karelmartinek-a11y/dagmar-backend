"""add employments and rebind attendance domain

Revision ID: 2026_05_20_0011
Revises: 2026_03_25_0010
Create Date: 2026-05-20 10:00:00.000000
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = "2026_05_20_0011"
down_revision = "0010"
branch_labels = None
depends_on = None


DEFAULT_EMPLOYMENT_START = date(2025, 1, 1)
DEFAULT_EMPLOYMENT_TITLE = "Výchozí úvazek"


def upgrade() -> None:
    op.create_table(
        "employments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("employment_type", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_employments_user_id", "employments", ["user_id"], unique=False)
    op.create_index("ix_employments_start_date", "employments", ["start_date"], unique=False)
    op.create_index("ix_employments_end_date", "employments", ["end_date"], unique=False)
    op.create_index("ix_employments_is_active", "employments", ["is_active"], unique=False)

    for table_name in (
        "attendance",
        "shift_plan",
        "shift_plan_month_instances",
        "attendance_locks",
        "attendance_reminder_events",
    ):
        op.add_column(table_name, sa.Column("employment_id", sa.Integer(), nullable=True))

    op.create_foreign_key("fk_attendance_employment_id", "attendance", "employments", ["employment_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_shift_plan_employment_id", "shift_plan", "employments", ["employment_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_shift_plan_month_instances_employment_id",
        "shift_plan_month_instances",
        "employments",
        ["employment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_attendance_locks_employment_id",
        "attendance_locks",
        "employments",
        ["employment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_attendance_reminder_events_employment_id",
        "attendance_reminder_events",
        "employments",
        ["employment_id"],
        ["id"],
        ondelete="CASCADE",
    )

    conn = op.get_bind()

    portal_users = conn.execute(
        text(
            """
            SELECT pu.id, pu.instance_id, COALESCE(i.employment_template, 'DPP_DPC') AS employment_type
            FROM portal_users pu
            LEFT JOIN instances i ON i.id = pu.instance_id
            ORDER BY pu.id
            """
        )
    ).fetchall()

    instance_to_employment_id: dict[str, int] = {}

    for user_id, instance_id, employment_type in portal_users:
        normalized_type = employment_type if employment_type in {"HPP", "DPP_DPC"} else "DPP_DPC"
        employment_id = conn.execute(
            text(
                """
                INSERT INTO employments (user_id, title, employment_type, start_date, end_date, is_active, created_at, updated_at)
                VALUES (:user_id, :title, :employment_type, :start_date, NULL, true, NOW(), NOW())
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "title": DEFAULT_EMPLOYMENT_TITLE,
                "employment_type": normalized_type,
                "start_date": DEFAULT_EMPLOYMENT_START,
            },
        ).scalar_one()
        if instance_id:
            instance_to_employment_id[str(instance_id)] = int(employment_id)

    orphan_instance_ids = set()
    for table_name in ("attendance", "shift_plan", "shift_plan_month_instances", "attendance_locks", "attendance_reminder_events"):
        rows = conn.execute(
            text(
                f"""
                SELECT DISTINCT instance_id
                FROM {table_name}
                WHERE instance_id IS NOT NULL
                """
            )
        ).fetchall()
        for (instance_id,) in rows:
            if instance_id and instance_id not in instance_to_employment_id:
                orphan_instance_ids.add(str(instance_id))

    for instance_id in sorted(orphan_instance_ids):
        instance_row = conn.execute(
            text("SELECT display_name, employment_template FROM instances WHERE id = :instance_id"),
            {"instance_id": instance_id},
        ).mappings().first()
        display_name = (instance_row["display_name"] if instance_row else None) or f"Docházka {instance_id[:8]}"
        employment_type = (instance_row["employment_template"] if instance_row else "DPP_DPC") or "DPP_DPC"
        normalized_type = employment_type if employment_type in {"HPP", "DPP_DPC"} else "DPP_DPC"
        user_id = conn.execute(
            text(
                """
                INSERT INTO portal_users (email, name, role, password_hash, is_active, instance_id, created_at, updated_at)
                VALUES (:email, :name, 'employee', NULL, false, :instance_id, NOW(), NOW())
                RETURNING id
                """
            ),
            {
                "email": f"orphan-{instance_id}@local.invalid",
                "name": display_name,
                "instance_id": instance_id,
            },
        ).scalar_one()
        employment_id = conn.execute(
            text(
                """
                INSERT INTO employments (user_id, title, employment_type, start_date, end_date, is_active, created_at, updated_at)
                VALUES (:user_id, :title, :employment_type, :start_date, NULL, true, NOW(), NOW())
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "title": DEFAULT_EMPLOYMENT_TITLE,
                "employment_type": normalized_type,
                "start_date": DEFAULT_EMPLOYMENT_START,
            },
        ).scalar_one()
        instance_to_employment_id[instance_id] = int(employment_id)

    for instance_id, employment_id in instance_to_employment_id.items():
        conn.execute(
            text("UPDATE attendance SET employment_id = :employment_id WHERE instance_id = :instance_id"),
            {"employment_id": employment_id, "instance_id": instance_id},
        )
        conn.execute(
            text("UPDATE shift_plan SET employment_id = :employment_id WHERE instance_id = :instance_id"),
            {"employment_id": employment_id, "instance_id": instance_id},
        )
        conn.execute(
            text("UPDATE shift_plan_month_instances SET employment_id = :employment_id WHERE instance_id = :instance_id"),
            {"employment_id": employment_id, "instance_id": instance_id},
        )
        conn.execute(
            text("UPDATE attendance_locks SET employment_id = :employment_id WHERE instance_id = :instance_id"),
            {"employment_id": employment_id, "instance_id": instance_id},
        )
        conn.execute(
            text("UPDATE attendance_reminder_events SET employment_id = :employment_id WHERE instance_id = :instance_id"),
            {"employment_id": employment_id, "instance_id": instance_id},
        )

    conn.execute(text("DELETE FROM attendance WHERE employment_id IS NULL"))
    conn.execute(text("DELETE FROM shift_plan WHERE employment_id IS NULL"))
    conn.execute(text("DELETE FROM shift_plan_month_instances WHERE employment_id IS NULL"))
    conn.execute(text("DELETE FROM attendance_locks WHERE employment_id IS NULL"))
    conn.execute(text("DELETE FROM attendance_reminder_events WHERE employment_id IS NULL"))

    with op.batch_alter_table("attendance") as batch:
        batch.alter_column("instance_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("employment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("uq_attendance_instance_date", type_="unique")
        batch.create_unique_constraint("uq_attendance_employment_date", ["employment_id", "date"])
    op.drop_index("ix_attendance_instance_date", table_name="attendance")
    op.create_index("ix_attendance_instance_date", "attendance", ["instance_id", "date"], unique=False)
    op.create_index("ix_attendance_employment_date", "attendance", ["employment_id", "date"], unique=False)

    with op.batch_alter_table("shift_plan") as batch:
        batch.alter_column("instance_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("employment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("uq_shift_plan_instance_date", type_="unique")
        batch.create_unique_constraint("uq_shift_plan_employment_date", ["employment_id", "date"])
    op.drop_index("ix_shift_plan_instance_id", table_name="shift_plan")
    op.create_index("ix_shift_plan_instance_id", "shift_plan", ["instance_id"], unique=False)
    op.create_index("ix_shift_plan_employment_id", "shift_plan", ["employment_id"], unique=False)

    with op.batch_alter_table("shift_plan_month_instances") as batch:
        batch.alter_column("instance_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("employment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("uq_shift_plan_month_instance", type_="unique")
        batch.create_unique_constraint("uq_shift_plan_month_employment", ["year", "month", "employment_id"])
    op.drop_index("ix_shift_plan_month_instances_instance_id", table_name="shift_plan_month_instances")
    op.create_index("ix_shift_plan_month_instances_instance_id", "shift_plan_month_instances", ["instance_id"], unique=False)
    op.create_index("ix_shift_plan_month_instances_employment_id", "shift_plan_month_instances", ["employment_id"], unique=False)

    with op.batch_alter_table("attendance_locks") as batch:
        batch.alter_column("instance_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("employment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("uq_attendance_lock_instance_month", type_="unique")
        batch.create_unique_constraint("uq_attendance_lock_employment_month", ["employment_id", "year", "month"])
    op.drop_index("ix_attendance_locks_instance_month", table_name="attendance_locks")
    op.create_index("ix_attendance_locks_instance_month", "attendance_locks", ["instance_id", "year", "month"], unique=False)
    op.create_index("ix_attendance_locks_employment_month", "attendance_locks", ["employment_id", "year", "month"], unique=False)

    with op.batch_alter_table("attendance_reminder_events") as batch:
        batch.alter_column("instance_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("employment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("uq_attendance_reminder_event_unique", type_="unique")
        batch.create_unique_constraint(
            "uq_attendance_reminder_event_unique",
            ["employment_id", "attendance_date", "reminder_type", "sequence_no"],
        )
    op.drop_index("ix_attendance_reminder_events_instance_date", table_name="attendance_reminder_events")
    op.create_index(
        "ix_attendance_reminder_events_instance_date",
        "attendance_reminder_events",
        ["instance_id", "attendance_date"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_reminder_events_employment_date",
        "attendance_reminder_events",
        ["employment_id", "attendance_date"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("Tato migrace neni bezpecne vratna.")
