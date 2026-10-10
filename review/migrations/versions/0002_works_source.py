"""작품별 수집 이력 works_source (#28)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('works_source',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('platform', sa.String(length=50), nullable=False),
    sa.Column('platform_work_id', sa.String(length=100), nullable=False),
    sa.Column('source_url', sa.String(length=768), nullable=False),
    sa.Column('works_id', sa.BigInteger(), nullable=True),
    sa.Column('title_snapshot', sa.String(length=255), nullable=True),
    sa.Column('artist_snapshot', sa.String(length=255), nullable=True),
    sa.Column('works_type', sa.String(length=20), nullable=True),
    sa.Column('search_keywords', sa.JSON(), nullable=True),
    sa.Column('last_crawled_at', sa.DateTime(), nullable=True),
    sa.Column('last_success_at', sa.DateTime(), nullable=True),
    sa.Column('crawl_status', sa.String(length=30), nullable=True),
    sa.Column('crawl_fail_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('relinked_to', sa.String(length=768), nullable=True),
    sa.Column('fingerprint_hash', sa.CHAR(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('platform', 'platform_work_id', name='uk_platform_work')
    )
    op.create_index('idx_status', 'works_source', ['crawl_status'], unique=False)
    op.create_index('idx_works', 'works_source', ['works_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_works', table_name='works_source')
    op.drop_index('idx_status', table_name='works_source')
    op.drop_table('works_source')
