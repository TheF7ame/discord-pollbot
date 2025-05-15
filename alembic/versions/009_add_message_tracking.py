"""add message tracking

Revision ID: 009
Revises: 008
Create Date: 2024-03-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from typing import Union, Sequence

# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Create poll_messages table
    op.create_table('polls_poll_messages',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('poll_id', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('message_type', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls_polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create ui_states table
    op.create_table('polls_ui_states',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('poll_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('state_data', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('last_interaction', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls_polls.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add indexes
    op.create_index('idx_poll_messages_poll_id', 'polls_poll_messages', ['poll_id'])
    op.create_index('idx_poll_messages_channel_id', 'polls_poll_messages', ['channel_id'])
    op.create_index('idx_ui_states_poll_id', 'polls_ui_states', ['poll_id'])
    op.create_index('idx_ui_states_user_id', 'polls_ui_states', ['user_id'])

def downgrade() -> None:
    op.drop_table('polls_ui_states')
    op.drop_table('polls_poll_messages') 