"""Persist user-facing chat identity and title separately from framework sessions."""
import sqlalchemy as sa
from alembic import op
revision='d8e9f0a1b2c3'
down_revision='c7d8e9f0a1b2'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('diagnosis_chat',sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('org_id',sa.Integer(),nullable=False),sa.Column('system_id',sa.Integer(),nullable=False),
        sa.Column('title',sa.String(120),nullable=False),sa.Column('deleted',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False))
    op.create_index('ix_diagnosis_chat_org_id','diagnosis_chat',['org_id'])
    op.create_index('ix_diagnosis_chat_system_id','diagnosis_chat',['system_id'])

def downgrade():
    op.drop_table('diagnosis_chat')
