"""趋势巡检：指标采样 + 早期预警（阈值告警的"提前量"）。

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_metric_samples_org", "metric_samples", ["org_id"])
    op.create_index("ix_metric_samples_system", "metric_samples", ["system_id"])
    op.create_index("ix_metric_samples_metric", "metric_samples", ["metric"])
    op.create_index("ix_metric_samples_observed", "metric_samples", ["observed_at"])
    op.create_index("ix_metric_samples_lookup", "metric_samples", ["system_id", "metric", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_metric_samples_lookup", table_name="metric_samples")
    op.drop_index("ix_metric_samples_observed", table_name="metric_samples")
    op.drop_index("ix_metric_samples_metric", table_name="metric_samples")
    op.drop_index("ix_metric_samples_system", table_name="metric_samples")
    op.drop_index("ix_metric_samples_org", table_name="metric_samples")
    op.drop_table("metric_samples")
