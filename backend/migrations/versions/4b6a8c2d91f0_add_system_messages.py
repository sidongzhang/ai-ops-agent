"""add system messages

Revision ID: 4b6a8c2d91f0
Revises: 23452711f18c, 9e4a8f7e6f31
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "4b6a8c2d91f0"
down_revision: Union[str, Sequence[str], None] = ("23452711f18c", "9e4a8f7e6f31")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("message_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("severity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("diagnosis", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("suggestion", sa.JSON(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("related", sa.JSON(), nullable=True),
        sa.Column("channels", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("ack_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("system_messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_system_messages_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_messages_message_type"), ["message_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_messages_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_messages_severity"), ["severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_messages_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_messages_system_id"), ["system_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("system_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_system_messages_system_id"))
        batch_op.drop_index(batch_op.f("ix_system_messages_status"))
        batch_op.drop_index(batch_op.f("ix_system_messages_severity"))
        batch_op.drop_index(batch_op.f("ix_system_messages_org_id"))
        batch_op.drop_index(batch_op.f("ix_system_messages_message_type"))
        batch_op.drop_index(batch_op.f("ix_system_messages_created_at"))
    op.drop_table("system_messages")
