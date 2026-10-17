"""Seven-day detailed telemetry and report-lifetime summaries."""
from alembic import op
import sqlalchemy as sa
revision='b2c3d4e5f6a'
down_revision='a1b2c3d4e5f'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('diagnosis_performance',
        sa.Column('report_id',sa.Integer(),sa.ForeignKey('diagnosis_reports.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('summary',sa.JSON(),nullable=False),sa.Column('detail',sa.JSON(none_as_null=True)),
        sa.Column('expires_at',sa.DateTime(),nullable=False))
    op.create_index('ix_diagnosis_performance_expires_at','diagnosis_performance',['expires_at'])

def downgrade():
    op.drop_table('diagnosis_performance')
