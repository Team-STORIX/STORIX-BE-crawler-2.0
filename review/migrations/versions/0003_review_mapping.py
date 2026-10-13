"""검수 판정 승격 매핑 review_mapping (#70)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('review_mapping',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('field', sa.String(length=50), nullable=False),
    sa.Column('raw_value', sa.String(length=255), nullable=False),
    sa.Column('decided_value', sa.String(length=100), nullable=False),
    sa.Column('decision_count', sa.Integer(), nullable=False),
    sa.Column('promoted_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('field', 'raw_value', name='uk_field_raw')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('review_mapping')
