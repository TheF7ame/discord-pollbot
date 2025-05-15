"""add description and show_votes_while_active columns to polls table

Revision ID: 012
Revises: 011
Create Date: 2024-05-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add description column (nullable)
    op.add_column('polls_polls', sa.Column('description', sa.String(), nullable=True))
    
    # Add show_votes_while_active column
    op.add_column('polls_polls', sa.Column('show_votes_while_active', sa.Boolean(), server_default='false', nullable=False))
    
    # Create index for faster lookups
    op.create_index('ix_polls_polls_show_votes_while_active', 'polls_polls', ['show_votes_while_active'])

def downgrade() -> None:
    # Drop the created index
    op.drop_index('ix_polls_polls_show_votes_while_active')
    
    # Drop the columns
    op.drop_column('polls_polls', 'show_votes_while_active')
    op.drop_column('polls_polls', 'description') 