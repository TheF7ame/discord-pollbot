"""add channel based polls

Revision ID: 005
Revises: 004
Create Date: 2024-02-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Create new tables first
    op.create_table(
        'polls_channel_leaderboards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'user_id', name='unique_channel_user')
    )
    
    # Add channel_id to polls table
    op.add_column('polls_polls', sa.Column('channel_id', sa.BigInteger(), nullable=True))
    op.create_index('ix_polls_polls_channel_id', 'polls_polls', ['channel_id'])
    
    # Create temporary table for user_scores
    op.execute("""
        CREATE TABLE polls_user_scores_new (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            points INTEGER NOT NULL,
            UNIQUE (channel_id, user_id)
        )
    """)
    
    # Copy data from old to new table with proper casting
    op.execute("""
        INSERT INTO polls_user_scores_new (channel_id, user_id, points)
        SELECT CAST(guild_id AS BIGINT), CAST(user_id AS BIGINT), points 
        FROM polls_user_scores
    """)
    
    # Drop old table and rename new one
    op.drop_table('polls_user_scores')
    op.execute('ALTER TABLE polls_user_scores_new RENAME TO polls_user_scores')
    
    # Drop old guild_leaderboards table
    op.drop_table('polls_guild_leaderboards')
    
    # Update existing records with a default channel_id
    op.execute("UPDATE polls_polls SET channel_id = 0 WHERE channel_id IS NULL")
    
    # Make columns non-nullable
    op.alter_column('polls_polls', 'channel_id', nullable=False)

def downgrade() -> None:
    # Create temporary table for user_scores
    op.execute("""
        CREATE TABLE polls_user_scores_new (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            points INTEGER NOT NULL,
            UNIQUE (guild_id, user_id)
        )
    """)
    
    # Copy data from channel-based to guild-based with proper casting
    op.execute("""
        INSERT INTO polls_user_scores_new (guild_id, user_id, points)
        SELECT CAST(channel_id AS BIGINT), CAST(user_id AS BIGINT), points 
        FROM polls_user_scores
    """)
    
    # Drop channel-based table and rename new one
    op.drop_table('polls_user_scores')
    op.execute('ALTER TABLE polls_user_scores_new RENAME TO polls_user_scores')
    
    # Create guild_leaderboards table
    op.create_table(
        'polls_guild_leaderboards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'user_id', name='unique_guild_user')
    )
    
    # Copy data from channel_leaderboards to guild_leaderboards with proper casting
    op.execute("""
        INSERT INTO polls_guild_leaderboards (guild_id, user_id, points)
        SELECT CAST(channel_id AS BIGINT), CAST(user_id AS BIGINT), points 
        FROM polls_channel_leaderboards
    """)
    
    # Drop channel_leaderboards table
    op.drop_table('polls_channel_leaderboards')
    
    # Remove channel_id from polls
    op.drop_index('ix_polls_polls_channel_id')
    op.drop_column('polls_polls', 'channel_id') 