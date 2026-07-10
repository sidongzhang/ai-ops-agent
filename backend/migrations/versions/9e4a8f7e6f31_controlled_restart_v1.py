"""controlled restart v1

Revision ID: 9e4a8f7e6f31
Revises: 238f4c603dd8
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "9e4a8f7e6f31"
down_revision: Union[str, Sequence[str], None] = "238f4c603dd8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "action_workflows"):
        op.create_table(
            "action_workflows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.Integer(), nullable=False),
            sa.Column("system_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
            sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
            sa.Column("thread_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("diagnosis", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("proposed_action", sa.JSON(), nullable=True),
            sa.Column("target_service", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("target_resource", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("execution_mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("execution_result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
            sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("action_workflows", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_action_workflows_org_id"), ["org_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_action_workflows_system_id"), ["system_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_action_workflows_requested_by_user_id"), ["requested_by_user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_action_workflows_approved_by_user_id"), ["approved_by_user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_action_workflows_thread_id"), ["thread_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_action_workflows_status"), ["status"], unique=False)

    inspector = sa.inspect(bind)
    if not _has_column(inspector, "systems", "restart_policy"):
        with op.batch_alter_table("systems", schema=None) as batch_op:
            batch_op.add_column(sa.Column("restart_policy", sa.JSON(), nullable=True))

    inspector = sa.inspect(bind)
    workflow_columns = {
        "requested_by_user_id": sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        "approved_by_user_id": sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        "target_service": sa.Column("target_service", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        "target_resource": sa.Column("target_resource", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        "execution_mode": sa.Column("execution_mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        "approved_at": sa.Column("approved_at", sa.DateTime(), nullable=True),
        "executed_at": sa.Column("executed_at", sa.DateTime(), nullable=True),
    }
    for column_name, column in workflow_columns.items():
        if not _has_column(inspector, "action_workflows", column_name):
            with op.batch_alter_table("action_workflows", schema=None) as batch_op:
                batch_op.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("action_workflows", schema=None) as batch_op:
        for index_name in (
            "ix_action_workflows_status",
            "ix_action_workflows_thread_id",
            "ix_action_workflows_approved_by_user_id",
            "ix_action_workflows_requested_by_user_id",
            "ix_action_workflows_system_id",
            "ix_action_workflows_org_id",
        ):
            try:
                batch_op.drop_index(index_name)
            except Exception:
                pass

    for table_name, column_name in (
        ("action_workflows", "executed_at"),
        ("action_workflows", "approved_at"),
        ("action_workflows", "execution_mode"),
        ("action_workflows", "target_resource"),
        ("action_workflows", "target_service"),
        ("action_workflows", "approved_by_user_id"),
        ("action_workflows", "requested_by_user_id"),
        ("systems", "restart_policy"),
    ):
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if _has_column(inspector, table_name, column_name):
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.drop_column(column_name)
