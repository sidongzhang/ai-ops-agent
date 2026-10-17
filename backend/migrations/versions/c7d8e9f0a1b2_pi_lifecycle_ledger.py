"""Persist runtime events, immutable budget reservations and ledger attempt IDs."""
import sqlalchemy as sa
from alembic import op
revision='c7d8e9f0a1b2'
down_revision='b6c7d8e9f0a1'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('diagnosis_usage_ledger',sa.Column('attempt_id',sa.String(100),nullable=True))
    op.create_index('ix_diagnosis_usage_ledger_attempt_id','diagnosis_usage_ledger',['attempt_id'],unique=True)
    op.create_table('diagnosis_budget_reservation',
        sa.Column('id',sa.String(64),primary_key=True),sa.Column('org_id',sa.Integer(),nullable=False),
        sa.Column('user_id',sa.Integer(),nullable=False),sa.Column('system_id',sa.Integer(),nullable=False),
        sa.Column('window_started_at',sa.DateTime(),nullable=False),sa.Column('reserved_tokens',sa.Integer(),nullable=False),
        sa.Column('reserved_cost',sa.Float(),nullable=False),sa.Column('status',sa.String(24),nullable=False),
        sa.Column('settled_tokens',sa.Integer(),nullable=False),sa.Column('settled_cost',sa.Float(),nullable=False),sa.Column('report_id',sa.Integer()))
    op.create_table('diagnosis_runtime_event',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('run_id',sa.String(100),nullable=False),sa.Column('sequence',sa.Integer(),nullable=False),
        sa.Column('org_id',sa.Integer(),nullable=False),sa.Column('system_id',sa.Integer(),nullable=False),
        sa.Column('kind',sa.String(64),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),sa.UniqueConstraint('run_id','sequence',name='uq_runtime_event_sequence'))

    for column in ('org_id','user_id','system_id','report_id'):
        op.create_index('ix_diagnosis_budget_reservation_'+column,'diagnosis_budget_reservation',[column])
    for column in ('run_id','org_id','system_id'):
        op.create_index('ix_diagnosis_runtime_event_'+column,'diagnosis_runtime_event',[column])


def downgrade():
    op.drop_table('diagnosis_runtime_event');op.drop_table('diagnosis_budget_reservation')
    op.drop_index('ix_diagnosis_usage_ledger_attempt_id',table_name='diagnosis_usage_ledger')
    op.drop_column('diagnosis_usage_ledger','attempt_id')
