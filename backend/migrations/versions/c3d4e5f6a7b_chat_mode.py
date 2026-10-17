"""Server-owned chat mode. In-flight runs retain admission snapshots."""
from alembic import op
import sqlalchemy as sa
revision='c3d4e5f6a7b'
down_revision='b2c3d4e5f6a'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('diagnosis_chat',sa.Column('interaction_mode',sa.String(16),nullable=False,server_default='auto'))

def downgrade():op.drop_column('diagnosis_chat','interaction_mode')
