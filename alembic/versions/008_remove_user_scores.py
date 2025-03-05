"""remove user scores table

Revision ID: 008
Revises: 007
Create Date: 2024-02-18 02:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Drop the user_scores table as it's no longer used
    op.drop_table('user_scores')

def downgrade() -> None:
    # Recreate the user_scores table in case of downgrade
    op.create_table('user_scores',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'channel_id'),
        sa.UniqueConstraint('channel_id', 'user_id', name='unique_channel_user_scores')
    ) 