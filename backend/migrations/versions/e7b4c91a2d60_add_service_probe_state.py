"""add persisted service probe state

Revision ID: e7b4c91a2d60
Revises: d3f2c8a71e09
Create Date: 2026-07-10 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "e7b4c91a2d60"
down_revision: Union[str, Sequence[str], None] = "d3f2c8a71e09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(
            sa.Column(
                "probe_status",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="passed",
            )
        )
        batch_op.add_column(
            sa.Column(
                "probe_detail",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(sa.Column("tested_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_services_enabled"), ["enabled"], unique=False)
        batch_op.create_index(batch_op.f("ix_services_probe_status"), ["probe_status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_services_probe_status"))
        batch_op.drop_index(batch_op.f("ix_services_enabled"))
        batch_op.drop_column("tested_at")
        batch_op.drop_column("probe_detail")
        batch_op.drop_column("probe_status")
        batch_op.drop_column("enabled")
