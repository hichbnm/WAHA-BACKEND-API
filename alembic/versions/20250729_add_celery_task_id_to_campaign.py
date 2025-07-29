"""
Add celery_task_id column to Campaign
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('campaigns', sa.Column('celery_task_id', sa.String(), nullable=True))

def downgrade():
    op.drop_column('campaigns', 'celery_task_id')
