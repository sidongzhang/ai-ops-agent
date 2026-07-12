"""add incidents and message incident_id

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-11 18:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("severity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("failed_services", sa.JSON(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_incidents_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_first_seen"), ["first_seen"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_last_seen"), ["last_seen"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_system_id"), ["system_id"], unique=False)

    with op.batch_alter_table("system_messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("incident_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_system_messages_incident_id", "incidents", ["incident_id"], ["id"])
        batch_op.create_index(batch_op.f("ix_system_messages_incident_id"), ["incident_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("system_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_system_messages_incident_id"))
        batch_op.drop_constraint("fk_system_messages_incident_id", type_="foreignkey")
        batch_op.drop_column("incident_id")
    op.drop_table("incidents")
