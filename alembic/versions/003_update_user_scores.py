"""update user scores table

Revision ID: 003
Revises: 002
Create Date: 2024-02-12 22:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Drop old table
    op.drop_table('user_scores')
    
    # Create new table
    op.create_table('user_scores',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('total_correct', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('user_id')
    )

def downgrade() -> None:
    op.drop_table('user_scores') 