"""add audit logs

Revision ID: d3f2c8a71e09
Revises: a91c20b73f41
Create Date: 2026-07-09 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "d3f2c8a71e09"
down_revision: Union[str, Sequence[str], None] = "a91c20b73f41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("actor_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_actor_id"), ["actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_actor_type"), ["actor_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_event_type"), ["event_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_system_id"), ["system_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_system_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_status"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_org_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_event_type"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_actor_type"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_actor_id"))
    op.drop_table("audit_logs")
