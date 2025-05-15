"""Add index column to poll_options table

Revision ID: 013
Revises: 012
Create Date: 2024-05-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add index column to the poll_options table
    op.add_column('polls_poll_options', sa.Column('index', sa.Integer(), nullable=False, server_default='0'))
    
    # Create an index on the column for faster lookups
    op.create_index('ix_polls_poll_options_index', 'polls_poll_options', ['index'])

def downgrade() -> None:
    # Drop the index and column in reverse order
    op.drop_index('ix_polls_poll_options_index')
    op.drop_column('polls_poll_options', 'index') 