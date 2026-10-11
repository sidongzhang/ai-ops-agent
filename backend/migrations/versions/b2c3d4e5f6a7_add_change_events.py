"""变更事件 change_events（用于"是不是刚发版导致的"关联）

Revision ID: b2c3d4e5f6a7
Revises: f8b9c0d1e2f3
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "f8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "change_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(128), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default="change"),
        sa.Column("title", sa.String(256), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(128), nullable=False, server_default=""),
        sa.Column("external_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_change_events_system_time", "change_events", ["system_id", "occurred_at"])
    op.create_index("ix_change_events_org", "change_events", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_change_events_system_time", table_name="change_events")
    op.drop_index("ix_change_events_org", table_name="change_events")
    op.drop_table("change_events")
