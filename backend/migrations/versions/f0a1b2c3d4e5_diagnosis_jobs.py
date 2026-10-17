"""Atomic report outbox for the dedicated diagnosis queue."""
from alembic import op
import sqlalchemy as sa
revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("diagnosis_job",
        sa.Column("report_id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("publish_after", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=False))
    for field in ("state", "publish_after"):
        op.create_index("ix_diagnosis_job_" + field, "diagnosis_job", [field])


def downgrade():
    op.drop_table("diagnosis_job")
