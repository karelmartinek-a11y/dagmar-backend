\"\"\"add shift plan tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-26

\"\"\"

from alembic import op
import sqlalchemy as sa


revision = \"0002\"
down_revision = \"0001\"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        \"shift_plan\",
        sa.Column(\"id\", sa.Integer(), primary_key=True),
        sa.Column(\"instance_id\", sa.String(), sa.ForeignKey(\"instances.id\", ondelete=\"CASCADE\"), nullable=False),
        sa.Column(\"date\", sa.Date(), nullable=False),
        sa.Column(\"arrival_time\", sa.String(), nullable=True),
        sa.Column(\"departure_time\", sa.String(), nullable=True),
        sa.Column(\"created_at\", sa.DateTime(timezone=True), server_default=sa.text(\"now()\"), nullable=False),
        sa.Column(\"updated_at\", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(\"instance_id\", \"date\", name=\"uq_shift_plan_instance_date\"),
    )
    op.create_index(op.f(\"ix_shift_plan_instance_id\"), \"shift_plan\", [\"instance_id\"], unique=False)
    op.create_index(op.f(\"ix_shift_plan_date\"), \"shift_plan\", [\"date\"], unique=False)

    op.create_table(
        \"shift_plan_month_instances\",
        sa.Column(\"id\", sa.Integer(), primary_key=True),
        sa.Column(\"year\", sa.Integer(), nullable=False),
        sa.Column(\"month\", sa.Integer(), nullable=False),
        sa.Column(\"instance_id\", sa.String(), sa.ForeignKey(\"instances.id\", ondelete=\"CASCADE\"), nullable=False),
        sa.Column(\"created_at\", sa.DateTime(timezone=True), server_default=sa.text(\"now()\"), nullable=False),
        sa.UniqueConstraint(\"year\", \"month\", \"instance_id\", name=\"uq_shift_plan_month_instance\"),
    )
    op.create_index(op.f(\"ix_shift_plan_month_instances_year\"), \"shift_plan_month_instances\", [\"year\"], unique=False)
    op.create_index(op.f(\"ix_shift_plan_month_instances_month\"), \"shift_plan_month_instances\", [\"month\"], unique=False)
    op.create_index(op.f(\"ix_shift_plan_month_instances_instance_id\"), \"shift_plan_month_instances\", [\"instance_id\"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f(\"ix_shift_plan_month_instances_instance_id\"), table_name=\"shift_plan_month_instances\")
    op.drop_index(op.f(\"ix_shift_plan_month_instances_month\"), table_name=\"shift_plan_month_instances\")
    op.drop_index(op.f(\"ix_shift_plan_month_instances_year\"), table_name=\"shift_plan_month_instances\")
    op.drop_table(\"shift_plan_month_instances\")

    op.drop_index(op.f(\"ix_shift_plan_date\"), table_name=\"shift_plan\")
    op.drop_index(op.f(\"ix_shift_plan_instance_id\"), table_name=\"shift_plan\")
    op.drop_table(\"shift_plan\")

