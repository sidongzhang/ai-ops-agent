"""Version opaque engine history; legacy rows remain Pydantic with unspecified version."""
import sqlalchemy as sa
from alembic import op

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diagnosis_run_state", sa.Column("engine", sa.String(32), nullable=False, server_default="pydantic"))
    op.add_column("diagnosis_run_state", sa.Column("engine_version", sa.String(64), nullable=False, server_default=""))
    op.add_column("diagnosis_run_state", sa.Column("history_schema_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("diagnosis_run_state", "history_schema_version")
    op.drop_column("diagnosis_run_state", "engine_version")
    op.drop_column("diagnosis_run_state", "engine")
