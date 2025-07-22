"""
Add message_queue table for persistent message queue
"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg

def upgrade():
    op.create_table(
        'message_queue',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('sender_number', sa.String, nullable=False),
        sa.Column('recipient', sa.String, nullable=False),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('media_url', sa.Text, nullable=True),
        sa.Column('status', sa.String, nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, nullable=False, server_default='0'),
    )

def downgrade():
    op.drop_table('message_queue')
