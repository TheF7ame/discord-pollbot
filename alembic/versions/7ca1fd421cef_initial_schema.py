"""initial schema

Revision ID: 7ca1fd421cef
Revises: 
Create Date: 2024-02-12 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7ca1fd421cef'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing tables if they exist
    conn = op.get_bind()
    conn.execute(sa.text('DROP TABLE IF EXISTS user_scores CASCADE'))
    conn.execute(sa.text('DROP TABLE IF EXISTS user_poll_selections CASCADE'))
    conn.execute(sa.text('DROP TABLE IF EXISTS poll_options CASCADE'))
    conn.execute(sa.text('DROP TABLE IF EXISTS polls CASCADE'))
    conn.execute(sa.text('DROP TABLE IF EXISTS poll_votes CASCADE'))
    conn.execute(sa.text('DROP TABLE IF EXISTS users CASCADE'))
    
    # Drop enum type if exists
    conn.execute(sa.text('DROP TYPE IF EXISTS pollstatus CASCADE'))
    
    # Create PollStatus enum type
    op.execute("CREATE TYPE pollstatus AS ENUM ('open', 'closed', 'revealed')")
    
    # Create polls table
    op.create_table('polls',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('question', sa.String(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=False),
        sa.Column('guild_id', sa.Integer(), nullable=False),
        sa.Column('max_selections', sa.Integer(), server_default='1', nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_revealed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('correct_answers', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create poll_options table
    op.create_table('poll_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('poll_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create user_poll_selections table
    op.create_table('user_poll_selections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('poll_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('selections', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create user_scores table
    op.create_table('user_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('guild_id', sa.Integer(), nullable=False),
        sa.Column('total_points', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_polls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('user_scores')
    op.drop_table('user_poll_selections')
    op.drop_table('poll_options')
    op.drop_table('polls')
    op.execute('DROP TYPE pollstatus')
