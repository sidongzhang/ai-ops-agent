"""Keep display drafts separate from authoritative answers and native checkpoints."""
from alembic import op
import sqlalchemy as sa
revision = "a1b2c3d4e5f"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("diagnosis_reports", sa.Column("answer_draft", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("diagnosis_reports", "answer_draft")
