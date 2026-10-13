"""诊断中断：取消字段 + 可恢复运行状态表

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_reports", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "diagnosis_reports", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("cancel_phase", sa.String(16), nullable=False, server_default=""),
    )

    op.create_table(
        "diagnosis_run_state",
        sa.Column("report_id", sa.Integer(), primary_key=True),
        sa.Column("messages_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("conversation_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("framework_cumulative_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("resumable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("diagnosis_run_state")
    op.drop_column("diagnosis_reports", "cancel_phase")
    op.drop_column("diagnosis_reports", "cancelled_at")
    op.drop_column("diagnosis_reports", "cancel_requested_at")
