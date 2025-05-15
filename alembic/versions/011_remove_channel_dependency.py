"""remove channel dependency and add poll_type based leaderboards

Revision ID: 011
Revises: 010
Create Date: 2024-05-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Step 1: Make channel_id nullable in polls table
    op.execute("""
        ALTER TABLE polls_polls ALTER COLUMN channel_id DROP NOT NULL
    """)
    
    # Step 2: Create a new table for poll_type based leaderboards
    op.execute("""
        CREATE TABLE IF NOT EXISTS polls_poll_type_leaderboards (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            poll_type VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            points INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            rank INTEGER NOT NULL,
            last_updated TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
            UNIQUE (guild_id, poll_type, user_id)
        )
    """)
    
    # Step 3: Create indexes for the new poll_type_leaderboards table
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_poll_type_leaderboards_guild_id ON polls_poll_type_leaderboards (guild_id);
        CREATE INDEX IF NOT EXISTS idx_poll_type_leaderboards_poll_type ON polls_poll_type_leaderboards (poll_type);
        CREATE INDEX IF NOT EXISTS idx_poll_type_leaderboards_user_id ON polls_poll_type_leaderboards (user_id);
    """)
    
    # Step 4: Migrate data from channel_leaderboards to poll_type_leaderboards
    # We'll group by user_id and poll_type
    op.execute("""
        INSERT INTO polls_poll_type_leaderboards (guild_id, poll_type, user_id, points, total_correct, rank, last_updated)
        SELECT 
            p.guild_id,
            p.poll_type,
            cl.user_id,
            SUM(cl.points) as points,
            SUM(cl.total_correct) as total_correct,
            RANK() OVER (PARTITION BY p.guild_id, p.poll_type ORDER BY SUM(cl.points) DESC) as rank,
            MAX(cl.last_updated) as last_updated
        FROM polls_channel_leaderboards cl
        JOIN polls_poll_messages pm ON cl.channel_id = pm.channel_id
        JOIN polls_polls p ON pm.poll_id = p.id
        GROUP BY p.guild_id, p.poll_type, cl.user_id
        ON CONFLICT (guild_id, poll_type, user_id) DO UPDATE
        SET points = EXCLUDED.points,
            total_correct = EXCLUDED.total_correct,
            rank = EXCLUDED.rank,
            last_updated = EXCLUDED.last_updated
    """)
    
    # Step 5: Drop the channel_leaderboards table
    op.execute("""
        DROP TABLE IF EXISTS polls_channel_leaderboards
    """)

def downgrade() -> None:
    # Step 1: Recreate the channel_leaderboards table
    op.execute("""
        CREATE TABLE IF NOT EXISTS polls_channel_leaderboards (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL,
            user_id VARCHAR NOT NULL,
            points INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            rank INTEGER NOT NULL,
            last_updated TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
            UNIQUE (channel_id, user_id)
        )
    """)
    
    # Step 2: Make channel_id required again in polls table
    op.execute("""
        UPDATE polls_polls SET channel_id = 0 WHERE channel_id IS NULL;
        ALTER TABLE polls_polls ALTER COLUMN channel_id SET NOT NULL
    """)
    
    # Step 3: Drop the new poll_type_leaderboards table
    op.execute("""
        DROP TABLE IF EXISTS polls_poll_type_leaderboards
    """) 