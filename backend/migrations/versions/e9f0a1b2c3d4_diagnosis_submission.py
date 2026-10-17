"""Persist unique external diagnosis submissions before budget admission."""
import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("diagnosis_submission",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    for name in ("org_id", "system_id", "report_id"):
        op.create_index("ix_diagnosis_submission_" + name, "diagnosis_submission", [name])


def downgrade():
    op.drop_table("diagnosis_submission")
