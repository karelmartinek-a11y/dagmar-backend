"""Add integration API tables.

Revision ID: 2026_06_22_0012
Revises: 2026_05_20_0011
Create Date: 2026-06-22 22:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_22_0012"
down_revision = "2026_05_20_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("allowed_employment_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("allowed_employee_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("ip_allowlist", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_integration_clients_name"),
    )
    op.create_index("ix_integration_clients_status", "integration_clients", ["status"], unique=False)
    op.create_index("ix_integration_clients_last_used_at", "integration_clients", ["last_used_at"], unique=False)

    op.create_table(
        "integration_client_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("integration_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("token_last4", sa.String(length=4), nullable=False),
        sa.Column("token_fingerprint", sa.String(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_integration_client_secrets_client_id", "integration_client_secrets", ["client_id"], unique=False)
    op.create_index(
        "ix_integration_client_secrets_token_prefix",
        "integration_client_secrets",
        ["token_prefix"],
        unique=False,
    )
    op.create_index(
        "ix_integration_client_secrets_token_fingerprint",
        "integration_client_secrets",
        ["token_fingerprint"],
        unique=False,
    )

    op.create_table(
        "integration_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("integration_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_integration_audit_log_client_id", "integration_audit_log", ["client_id"], unique=False)
    op.create_index("ix_integration_audit_log_requested_at", "integration_audit_log", ["requested_at"], unique=False)
    op.create_index("ix_integration_audit_log_path", "integration_audit_log", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_integration_audit_log_path", table_name="integration_audit_log")
    op.drop_index("ix_integration_audit_log_requested_at", table_name="integration_audit_log")
    op.drop_index("ix_integration_audit_log_client_id", table_name="integration_audit_log")
    op.drop_table("integration_audit_log")

    op.drop_index("ix_integration_client_secrets_token_fingerprint", table_name="integration_client_secrets")
    op.drop_index("ix_integration_client_secrets_token_prefix", table_name="integration_client_secrets")
    op.drop_index("ix_integration_client_secrets_client_id", table_name="integration_client_secrets")
    op.drop_table("integration_client_secrets")

    op.drop_index("ix_integration_clients_last_used_at", table_name="integration_clients")
    op.drop_index("ix_integration_clients_status", table_name="integration_clients")
    op.drop_table("integration_clients")

