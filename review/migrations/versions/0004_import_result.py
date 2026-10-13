"""BE 반영 결과 · 시각 works_staging.import_result · imported_at (#77)

주간 "크롤러 작품 추가" 알림이 새로 만든 작품(CREATED)만 고르려고 남긴다.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('works_staging', sa.Column('import_result', sa.String(length=20), nullable=True))
    op.add_column('works_staging', sa.Column('imported_at', sa.DateTime(), nullable=True))
    op.create_index('idx_imported_at', 'works_staging', ['imported_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_imported_at', table_name='works_staging')
    op.drop_column('works_staging', 'imported_at')
    op.drop_column('works_staging', 'import_result')
