"""staging DB 첫 구조 (staging_run · works_staging · review_decision)

Revision ID: 0001
Revises: 
Create Date: 2026-10-10

Alembic 도입(#52) 전에 review/schema.sql 로 만들던 구조 그대로다. 이미 테이블이 있는 DB 는
ensure_schema() 가 이 리비전으로 stamp 만 한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('review_decision',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('staging_id', sa.BigInteger(), nullable=False),
    sa.Column('field', sa.String(length=50), nullable=False),
    sa.Column('raw_value', sa.Text(), nullable=True),
    sa.Column('decided_value', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.String(length=100), nullable=True),
    sa.Column('decided_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_field', 'review_decision', ['field'], unique=False)
    op.create_index('idx_staging', 'review_decision', ['staging_id'], unique=False)
    op.create_table('staging_run',
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('source_file', sa.Text(), nullable=True),
    sa.Column('item_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('held', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('hold_reasons', sa.JSON(), nullable=True),
    sa.Column('released_by', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('run_id')
    )
    op.create_index('idx_source_created', 'staging_run', ['source', 'created_at'], unique=False)
    op.create_table('works_staging',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('source_url', sa.String(length=768), nullable=False),
    sa.Column('platform', sa.Text(), nullable=True),
    sa.Column('works_name', sa.Text(), nullable=True),
    sa.Column('artist_name', sa.Text(), nullable=True),
    sa.Column('author', sa.Text(), nullable=True),
    sa.Column('illustrator', sa.Text(), nullable=True),
    sa.Column('original_author', sa.Text(), nullable=True),
    sa.Column('age_classification', sa.Text(), nullable=True),
    sa.Column('genre', sa.Text(), nullable=True),
    sa.Column('works_type', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('thumbnail_url', sa.Text(), nullable=True),
    sa.Column('hashtags', sa.JSON(), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
    sa.Column('normalized', sa.JSON(), nullable=True),
    sa.Column('violations', sa.JSON(), nullable=True),
    sa.Column('llm_verdict', sa.JSON(), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('reviewed_by', sa.String(length=100), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('imported_works_id', sa.BigInteger(), nullable=True),
    sa.Column('import_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_url', name='uk_source_url')
    )
    op.create_index('idx_run', 'works_staging', ['run_id'], unique=False)
    op.create_index('idx_status', 'works_staging', ['status'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_status', table_name='works_staging')
    op.drop_index('idx_run', table_name='works_staging')
    op.drop_table('works_staging')
    op.drop_index('idx_source_created', table_name='staging_run')
    op.drop_table('staging_run')
    op.drop_index('idx_staging', table_name='review_decision')
    op.drop_index('idx_field', table_name='review_decision')
    op.drop_table('review_decision')
