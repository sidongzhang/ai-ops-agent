"""用量完整性 / 用量账本 / 预算账本 / 运行心跳

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 心跳：用于判定"是否还有活着的 worker 在推进"，reaper 不再只看创建时间
    op.add_column("diagnosis_reports", sa.Column("runner_id", sa.String(64), nullable=False, server_default=""))
    op.add_column(
        "diagnosis_reports", sa.Column("runner_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    # 2) 用量完整性：有 usage 不等于用量完整（取消落在模型请求在途时该请求用量不可得）
    op.add_column(
        "diagnosis_reports", sa.Column("usage_complete", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("usage_gap_reason", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column("diagnosis_reports", sa.Column("cost_usd_estimated", sa.Numeric(12, 6), nullable=True))
    op.add_column(
        "diagnosis_reports",
        sa.Column("price_table_version", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_diagnosis_reports_heartbeat", "diagnosis_reports", ["runner_heartbeat_at"])

    # 3) 用量账本：每次 run 一行"增量"，会话累计 = SUM(delta)，杜绝 resume 时重复累加
    op.create_table(
        "diagnosis_usage_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("run_kind", sa.String(24), nullable=False, server_default="initial"),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_cache_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_cache_write", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_delta", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("usage_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("estimated_portion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_usage_ledger_session",
        "diagnosis_usage_ledger",
        ["org_id", "system_id", "conversation_id", "created_at"],
    )
    op.create_index("ix_usage_ledger_report", "diagnosis_usage_ledger", ["report_id"])

    # 4) 预算账本：提交时原子预留、结束时结算，覆盖并发请求
    op.create_table(
        "diagnosis_budget_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settled_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_cost", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("settled_cost", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "user_id", "system_id", "window_started_at", name="uq_budget_window"),
    )


def downgrade() -> None:
    op.drop_table("diagnosis_budget_ledger")
    op.drop_index("ix_usage_ledger_report", table_name="diagnosis_usage_ledger")
    op.drop_index("ix_usage_ledger_session", table_name="diagnosis_usage_ledger")
    op.drop_table("diagnosis_usage_ledger")
    op.drop_index("ix_diagnosis_reports_heartbeat", table_name="diagnosis_reports")
    for col in ("price_table_version", "cost_usd_estimated", "usage_gap_reason", "usage_complete",
                "runner_heartbeat_at", "runner_id"):
        op.drop_column("diagnosis_reports", col)
