"""instance templates + deactivated + app settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE instance_status ADD VALUE IF NOT EXISTS 'DEACTIVATED'")

    with op.batch_alter_table("instances") as batch:
        try:
            batch.drop_constraint("uq_instances_client_fingerprint", type_="unique")
        except Exception:
            pass
        batch.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "employment_template",
                sa.String(length=16),
                nullable=False,
                server_default="DPP_DPC",
            )
        )

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("afternoon_cutoff_minutes", sa.Integer(), nullable=False, server_default=str(17 * 60)),
    )
    op.execute("INSERT INTO app_settings (id, afternoon_cutoff_minutes) VALUES (1, %s)" % (17 * 60))


def downgrade() -> None:
    op.drop_table("app_settings")
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("employment_template")
        batch.drop_column("deactivated_at")
        batch.drop_column("revoked_at")
        batch.drop_column("activated_at")
    # unique constraint intentionally not restored
