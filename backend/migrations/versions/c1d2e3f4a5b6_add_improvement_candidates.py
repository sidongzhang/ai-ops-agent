"""自改进候选表 improvement_candidates + diagnosis_reports 反馈字段

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_reports",
        sa.Column("feedback", sa.String(16), nullable=False, server_default=""),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("feedback_note", sa.String(2000), nullable=False, server_default=""),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_diagnosis_reports_feedback", "diagnosis_reports", ["feedback"])

    op.create_table(
        "improvement_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="feedback_down"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("question", sa.String(1000), nullable=False, server_default=""),
        sa.Column("answer_excerpt", sa.String(2000), nullable=False, server_default=""),
        sa.Column("symptom", sa.String(500), nullable=False, server_default=""),
        sa.Column("root_cause", sa.String(2000), nullable=False, server_default=""),
        sa.Column("remedy", sa.String(2000), nullable=False, server_default=""),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("review_note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("memory_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_improvement_candidates_org", "improvement_candidates", ["org_id"])
    op.create_index("ix_improvement_candidates_system", "improvement_candidates", ["system_id"])
    op.create_index("ix_improvement_candidates_status", "improvement_candidates", ["status"])
    op.create_index("ix_improvement_candidates_source", "improvement_candidates", ["source"])
    op.create_index("ix_improvement_candidates_report", "improvement_candidates", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_improvement_candidates_report", table_name="improvement_candidates")
    op.drop_index("ix_improvement_candidates_source", table_name="improvement_candidates")
    op.drop_index("ix_improvement_candidates_status", table_name="improvement_candidates")
    op.drop_index("ix_improvement_candidates_system", table_name="improvement_candidates")
    op.drop_index("ix_improvement_candidates_org", table_name="improvement_candidates")
    op.drop_table("improvement_candidates")

    op.drop_index("ix_diagnosis_reports_feedback", table_name="diagnosis_reports")
    op.drop_column("diagnosis_reports", "feedback_at")
    op.drop_column("diagnosis_reports", "feedback_note")
    op.drop_column("diagnosis_reports", "feedback")
