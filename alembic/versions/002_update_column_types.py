"""update column types to bigint

Revision ID: 002
Revises: 7ca1fd421cef
Create Date: 2024-02-12 21:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '7ca1fd421cef'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Drop existing tables
    op.drop_table('polls_user_scores')
    op.drop_table('polls_user_poll_selections')
    op.drop_table('polls_poll_options')
    op.drop_table('polls_polls')
    
    # Recreate tables with BIGINT
    op.create_table('polls_polls',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('question', sa.String(), nullable=False),
        sa.Column('creator_id', sa.BigInteger(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('max_selections', sa.BigInteger(), server_default='1', nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_revealed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('correct_answers', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_table('polls_poll_options',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('poll_id', sa.BigInteger(), nullable=False),
        sa.Column('text', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls_polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_table('polls_user_poll_selections',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('poll_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('selections', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls_polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_table('polls_user_scores',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('total_points', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('total_correct', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('total_polls', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    # This is a destructive change, no downgrade path
    pass 