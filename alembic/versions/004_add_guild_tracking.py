"""add guild tracking

Revision ID: 004
Revises: 003
Create Date: 2024-02-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Create guilds table
    op.create_table('guilds',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('guild_id')
    )

    # Create guild_leaderboards table
    op.create_table('guild_leaderboards',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.guild_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'user_id', name='unique_guild_user')
    )

    # Drop and recreate user_scores table with guild_id as part of primary key
    op.drop_table('user_scores')
    op.create_table('user_scores',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.guild_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'guild_id')
    )

    # Add foreign key and index to polls.guild_id
    op.create_foreign_key(
        'fk_polls_guild_id', 'polls', 'guilds',
        ['guild_id'], ['guild_id'], ondelete='CASCADE'
    )
    op.create_index('ix_polls_guild_id', 'polls', ['guild_id'])

def downgrade() -> None:
    # Remove foreign key and index from polls
    op.drop_index('ix_polls_guild_id')
    op.drop_constraint('fk_polls_guild_id', 'polls', type_='foreignkey')

    # Drop new tables
    op.drop_table('guild_leaderboards')
    op.drop_table('user_scores')
    op.drop_table('guilds')

    # Recreate original user_scores table
    op.create_table('user_scores',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('total_correct', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('user_id')
    ) 