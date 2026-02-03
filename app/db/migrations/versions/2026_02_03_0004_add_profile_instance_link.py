"""add profile instance link

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-03

"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "instances",
        sa.Column("profile_instance_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_instances_profile_instance_id",
        "instances",
        "instances",
        ["profile_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_instances_profile_instance_id"),
        "instances",
        ["profile_instance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_instances_profile_instance_id"), table_name="instances")
    op.drop_constraint("fk_instances_profile_instance_id", "instances", type_="foreignkey")
    op.drop_column("instances", "profile_instance_id")
