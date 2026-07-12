"""add knowledge refs to diagnosis reports

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-11 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("diagnosis_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("knowledge_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("diagnosis_reports", schema=None) as batch_op:
        batch_op.drop_column("knowledge_refs")
