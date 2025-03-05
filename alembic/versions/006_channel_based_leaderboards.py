"""channel based leaderboards

Revision ID: 006
Revises: 005
Create Date: 2024-02-16 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Drop existing user_scores table
    op.drop_table('user_scores')
    
    # Create new channel-based user_scores table with additional fields
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
    
    # Drop existing channel_leaderboards table if it exists
    try:
        op.drop_table('channel_leaderboards')
    except:
        pass
    
    # Create new channel_leaderboards table
    op.create_table('channel_leaderboards',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'user_id', name='unique_channel_user_leaderboard')
    )

def downgrade() -> None:
    # Drop new tables
    op.drop_table('channel_leaderboards')
    op.drop_table('user_scores')
    
    # Recreate old user_scores table
    op.create_table('user_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'user_id', name='unique_channel_user_basic')
    )

    # Recreate old guild-based tables
    op.create_table('user_scores',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('poll_type', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.guild_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'guild_id', 'poll_type'),
        sa.UniqueConstraint('guild_id', 'user_id', 'poll_type', name='unique_guild_user_poll_type')
    )
    
    op.create_table('guild_leaderboards',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('poll_type', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.guild_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'poll_type', 'user_id', name='unique_guild_poll_type_user')
    ) 