"""继续（resume）：源报告唯一约束 + 报告用量完整性字段暴露

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_reports", sa.Column("resumed_from_report_id", sa.Integer(), nullable=True)
    )
    # 唯一约束 = 幂等基础：同一源报告只能被继续一次（重复点击/并发点击都只有一个赢）
    op.create_index(
        "uq_diagnosis_resumed_from",
        "diagnosis_reports",
        ["resumed_from_report_id"],
        unique=True,
        postgresql_where=sa.text("resumed_from_report_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_diagnosis_resumed_from", table_name="diagnosis_reports")
    op.drop_column("diagnosis_reports", "resumed_from_report_id")
