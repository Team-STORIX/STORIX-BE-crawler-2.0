"""staging DB 구조 · 마이그레이션 (#52). REVIEW_TEST_MYSQL_HOST 가 있을 때만 돈다."""
import os

import pytest

from test_review import needs_mysql

pytest.importorskip('alembic')

DB = 'storix_staging_migration_test'


@pytest.fixture
def staging(monkeypatch):
    import config
    from review import store as store_mod
    monkeypatch.setitem(config.MYSQL_CONFIG, 'host', os.environ['REVIEW_TEST_MYSQL_HOST'])
    monkeypatch.setitem(config.MYSQL_CONFIG, 'port', int(os.getenv('REVIEW_TEST_MYSQL_PORT', '3306')))
    monkeypatch.setitem(config.MYSQL_CONFIG, 'user', 'root')
    monkeypatch.setitem(config.MYSQL_CONFIG, 'password', os.getenv('REVIEW_TEST_MYSQL_PASSWORD', ''))
    monkeypatch.setattr(store_mod, 'STAGING_DATABASE', DB)
    _sql(f'DROP DATABASE IF EXISTS `{DB}`')
    try:
        yield store_mod
    finally:
        _sql(f'DROP DATABASE IF EXISTS `{DB}`')


def _sql(*stmts, database=None):
    import mysql.connector
    import config
    cfg = {k: v for k, v in config.MYSQL_CONFIG.items() if k != 'database'}
    conn = mysql.connector.connect(**cfg, **({'database': database} if database else {}))
    try:
        cur = conn.cursor()
        rows = None
        for s in stmts:
            cur.execute(s)
            rows = cur.fetchall() if cur.with_rows else rows
        conn.commit()
        return rows
    finally:
        conn.close()


def _version():
    return _sql('SELECT version_num FROM alembic_version', database=DB)


def _tables():
    return {r[0] for r in _sql('SHOW TABLES', database=DB)}


@needs_mysql
def test_fresh_db_upgrades_to_head(staging):
    staging.ensure_schema()
    assert _tables() == {'alembic_version', 'staging_run', 'works_staging', 'review_decision', 'works_source'}
    assert _version() == [('0002',)]
    staging.ensure_schema()  # 다시 불러도 그대로
    assert _version() == [('0002',)]


@needs_mysql
def test_models_match_migrations(staging):
    """모델(review/models.py)을 고치고 리비전을 안 만들면 실패한다 (BE 의 ddl-auto=validate 역할)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine
    from review.models import Base

    staging.ensure_schema()
    engine = create_engine(staging.staging_url())
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={'compare_type': True, 'compare_server_default': True})
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


@needs_mysql
@pytest.mark.parametrize('legacy_tables,baseline', [
    (('staging_run', 'works_staging', 'review_decision'), '0001'),   # #28 전에 만든 DB
    (('staging_run', 'works_staging', 'review_decision', 'works_source'), '0002'),
])
def test_db_made_before_alembic_is_stamped_then_upgraded(staging, legacy_tables, baseline):
    """Alembic 도입 전 DB(테이블은 있고 alembic_version 은 없음)는 있는 테이블로 기준 리비전을 찍고 올린다."""
    staging.ensure_schema()
    keep = set(legacy_tables)
    _sql(*[f'DROP TABLE {t}' for t in ('alembic_version', 'works_source') if t not in keep], database=DB)
    _sql("INSERT INTO staging_run (run_id, source) VALUES ('r1', 'legacy')", database=DB)

    staging.ensure_schema()
    assert _version() == [('0002',)]
    assert 'works_source' in _tables()
    # 기존 데이터는 그대로
    assert _sql("SELECT source FROM staging_run WHERE run_id = 'r1'", database=DB) == [('legacy',)]
