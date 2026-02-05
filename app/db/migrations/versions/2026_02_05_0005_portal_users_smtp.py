"""Portal users + reset tokens + SMTP settings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    role_enum = ENUM("employee", name="portal_user_role", create_type=False)
    role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "portal_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=160), nullable=False, unique=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("instance_id", sa.String(length=36), sa.ForeignKey("instances.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "portal_user_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.add_column("app_settings", sa.Column("smtp_host", sa.String(length=255), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_port", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_username", sa.String(length=255), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_password", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_security", sa.String(length=16), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_from_email", sa.String(length=255), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_from_name", sa.String(length=255), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "smtp_updated_at")
    op.drop_column("app_settings", "smtp_from_name")
    op.drop_column("app_settings", "smtp_from_email")
    op.drop_column("app_settings", "smtp_security")
    op.drop_column("app_settings", "smtp_password")
    op.drop_column("app_settings", "smtp_username")
    op.drop_column("app_settings", "smtp_port")
    op.drop_column("app_settings", "smtp_host")

    op.drop_table("portal_user_reset_tokens")
    op.drop_table("portal_users")
    op.execute("DROP TYPE IF EXISTS portal_user_role")
