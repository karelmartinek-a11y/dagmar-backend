"""Add attendance write audit fields to integration audit log.

Revision ID: 2026_06_23_0014
Revises: 2026_06_23_0013
Create Date: 2026-06-23 12:40:00
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_06_23_0014"
down_revision = "2026_06_23_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_audit_log", sa.Column("operation", sa.String(length=32), nullable=True))
    op.add_column("integration_audit_log", sa.Column("attendance_id", sa.Integer(), nullable=True))
    op.add_column("integration_audit_log", sa.Column("employment_id", sa.Integer(), nullable=True))
    op.add_column("integration_audit_log", sa.Column("attendance_date", sa.Date(), nullable=True))
    op.add_column("integration_audit_log", sa.Column("expected_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integration_audit_log", sa.Column("before_state", sa.JSON(), nullable=True))
    op.add_column("integration_audit_log", sa.Column("after_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("integration_audit_log", "after_state")
    op.drop_column("integration_audit_log", "before_state")
    op.drop_column("integration_audit_log", "expected_updated_at")
    op.drop_column("integration_audit_log", "attendance_date")
    op.drop_column("integration_audit_log", "employment_id")
    op.drop_column("integration_audit_log", "attendance_id")
    op.drop_column("integration_audit_log", "operation")
