"""add diagnosis reports

Revision ID: f1a2b3c4d5e6
Revises: e7b4c91a2d60
Create Date: 2026-07-11 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7b4c91a2d60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("report_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("answer", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("template_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("template_description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("evidence_sources", sa.JSON(), nullable=True),
        sa.Column("evidence_steps", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("diagnosis_reports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_report_type"), ["report_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_system_id"), ["system_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_diagnosis_reports_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("diagnosis_reports")
