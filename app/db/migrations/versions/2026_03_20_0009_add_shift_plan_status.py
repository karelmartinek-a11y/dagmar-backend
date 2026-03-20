"""add status to shift plan

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shift_plan", sa.Column("status", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("shift_plan", "status")
