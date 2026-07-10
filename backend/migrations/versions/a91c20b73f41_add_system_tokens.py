"""add system tokens

Revision ID: a91c20b73f41
Revises: 4b6a8c2d91f0
Create Date: 2026-07-09 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401


revision: str = "a91c20b73f41"
down_revision: Union[str, Sequence[str], None] = "4b6a8c2d91f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("allowed_ips", sa.JSON(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["system_id"], ["systems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("system_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_system_tokens_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_tokens_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_tokens_system_id"), ["system_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_tokens_token_hash"), ["token_hash"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("system_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_system_tokens_token_hash"))
        batch_op.drop_index(batch_op.f("ix_system_tokens_system_id"))
        batch_op.drop_index(batch_op.f("ix_system_tokens_status"))
        batch_op.drop_index(batch_op.f("ix_system_tokens_org_id"))
    op.drop_table("system_tokens")
