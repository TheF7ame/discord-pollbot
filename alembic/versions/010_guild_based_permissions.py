"""guild based permissions

Revision ID: 010
Revises: 009
Create Date: 2024-02-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Create admin_roles table if not exists
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_roles (
            guild_id BIGINT NOT NULL,
            poll_type VARCHAR NOT NULL,
            role_id BIGINT NOT NULL,
            PRIMARY KEY (guild_id, poll_type),
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
        )
    """)
    
    # Add poll_type column to polls table if not exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'polls' AND column_name = 'poll_type'
            ) THEN
                ALTER TABLE polls ADD COLUMN poll_type VARCHAR;
            END IF;
        END
        $$;
    """)
    
    # Clean up duplicate active polls before adding constraint
    op.execute("""
        WITH ranked_polls AS (
            SELECT id,
                   guild_id,
                   poll_type,
                   is_active,
                   ROW_NUMBER() OVER (
                       PARTITION BY guild_id, poll_type, is_active
                       ORDER BY created_at DESC
                   ) as rn
            FROM polls
            WHERE is_active = true
        )
        UPDATE polls
        SET is_active = false
        WHERE id IN (
            SELECT id
            FROM ranked_polls
            WHERE rn > 1
        );
    """)
    
    # Create unique constraint for active polls per guild and poll type
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'uq_active_poll_per_guild_type'
            ) THEN
                CREATE UNIQUE INDEX uq_active_poll_per_guild_type
                ON polls (guild_id, poll_type)
                WHERE is_active = true;
            END IF;
        END
        $$;
    """)
    
    # Add poll_type to user_scores if not exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'user_scores' AND column_name = 'poll_type'
            ) THEN
                ALTER TABLE user_scores ADD COLUMN poll_type VARCHAR;
            END IF;
        END
        $$;
    """)

def downgrade() -> None:
    # Remove guild-based changes
    op.execute("DROP TABLE IF EXISTS admin_roles")
    op.execute("DROP INDEX IF EXISTS uq_active_poll_per_guild_type")
    op.execute("ALTER TABLE polls DROP COLUMN IF EXISTS poll_type")
    op.execute("ALTER TABLE user_scores DROP COLUMN IF EXISTS poll_type") 