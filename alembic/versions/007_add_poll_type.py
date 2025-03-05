"""add poll type column

Revision ID: 007
Revises: 006
Create Date: 2024-02-18 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add poll_type column
    op.add_column('polls', sa.Column('poll_type', sa.String(), nullable=True))
    
    # Create index for faster lookups
    op.create_index('ix_polls_poll_type', 'polls', ['poll_type'])

def downgrade() -> None:
    op.drop_index('ix_polls_poll_type')
    op.drop_column('polls', 'poll_type') 