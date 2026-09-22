"""Link diagnosis reports to external business messages.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_reports",
        sa.Column("external_request_id", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index(
        op.f("ix_diagnosis_reports_external_request_id"),
        "diagnosis_reports",
        ["external_request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_diagnosis_reports_external_request_id"), table_name="diagnosis_reports")
    op.drop_column("diagnosis_reports", "external_request_id")
