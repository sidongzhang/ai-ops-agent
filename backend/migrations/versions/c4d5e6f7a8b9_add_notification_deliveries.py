"""add notification deliveries

Revision ID: c4d5e6f7a8b9
Revises: b8c9d0e1f2a3
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("recipient", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("subject", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("body", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("provider_response", sa.JSON(), nullable=True),
        sa.Column("provider_message_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failed_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["system_messages.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notification_deliveries_channel"), ["channel"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_message_id"), ["message_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_sent_at"), ["sent_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_notification_deliveries_system_id"), ["system_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_system_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_status"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_sent_at"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_org_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_message_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_created_at"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_channel"))
    op.drop_table("notification_deliveries")
