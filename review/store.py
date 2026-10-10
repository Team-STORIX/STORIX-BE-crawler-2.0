"""works_staging 저장소.

서비스 DB 와 같은 MySQL 인스턴스지만 DB 를 분리한다(STAGING_DATABASE_NAME, 기본 storix_staging_{STORIX_ENV}).
BE 의 ddl-auto / Flyway baseline 과 섞이지 않게 하려는 것이다.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector

from config import MYSQL_CONFIG
from review import env as storix_env
from review.landing import canonical_landing_url, platform_work_key
from review.rules import AUTO_PASS, NEEDS_REVIEW, REJECTED, Verdict

APPROVED = 'APPROVED'
IMPORTED = 'IMPORTED'
SKIPPED = 'SKIPPED'   # BE 가 만들지 않기로 한 건 (단행본인데 같은 웹소설이 이미 있음). 다시 보내지 않는다
IMPORTABLE = (AUTO_PASS, APPROVED)

# import 상태가 환경마다 달라 dev / prod 를 다른 DB 에 쌓는다 (review/env.py)
STAGING_DATABASE = storix_env.staging_database(storix_env.target())
SCHEMA_FILE = Path(__file__).with_name('schema.sql')

# 수집 이력 (works_source) 상태. 나머지는 crawler/report.py 상태 코드를 그대로 쓴다
SOURCE_SUCCESS = 'SUCCESS'
SOURCE_RELINKED = 'RELINKED'   # 링크 복구로 새 링크를 찾았다. 새 링크는 다음 적재 때 따로 한 행이 된다
# 링크가 이만큼 연달아 깨지면 검수 대기로 돌려 사람이 본다
MAX_SOURCE_FAILS = 3
# 수집 내용 비교에서 빼는 값. 수집할 때마다 바뀌지만 작품 정보가 아니다
VOLATILE_KEYS = ('crawled_at', 'mode', 'schema_version')


def content_fingerprint(item: dict) -> str:
    """수집 내용 해시 (제목 · 작가 · 연령 · 장르 · 소개 · 썸네일 · 해시태그 등). 수집 시각 · 모드는 뺀다."""
    body = {k: v for k, v in item.items() if k not in VOLATILE_KEYS}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


RAW_COLUMNS = (
    'platform', 'works_name', 'artist_name', 'author', 'illustrator', 'original_author',
    'age_classification', 'genre', 'works_type', 'description', 'thumbnail_url',
)


def connect():
    cfg = {**MYSQL_CONFIG, 'database': STAGING_DATABASE}
    return mysql.connector.connect(**cfg)


def ensure_schema() -> None:
    """staging DB 와 테이블을 만든다. 전부 IF NOT EXISTS 라 매번 불러도 된다."""
    server_cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != 'database'}
    conn = mysql.connector.connect(**server_cfg)
    try:
        cur = conn.cursor()
        cur.execute(f'CREATE DATABASE IF NOT EXISTS `{STAGING_DATABASE}` '
                    'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        cur.execute(f'USE `{STAGING_DATABASE}`')
        for stmt in _statements(SCHEMA_FILE.read_text(encoding='utf-8')):
            cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith('--')]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(row: dict, *cols: str) -> dict:
    for c in cols:
        if isinstance(row.get(c), (str, bytes, bytearray)):
            row[c] = json.loads(row[c])
    return row


class StagingStore:
    def __init__(self, conn):
        self._conn = conn

    def _cursor(self):
        return self._conn.cursor(dictionary=True)

    # ------------------------------------------------------------------ runs

    def previous_count(self, source: str) -> int | None:
        cur = self._cursor()
        cur.execute('SELECT item_count FROM staging_run WHERE source = %s '
                    'ORDER BY created_at DESC LIMIT 1', (source,))
        row = cur.fetchone()
        return row['item_count'] if row else None

    def save_run(self, source: str, source_file: str | None,
                 items: list[dict], verdicts: list[Verdict], hold_reasons: list[str]) -> str:
        """런 하나를 통째로 적재한다. 같은 source_url 은 갱신된다."""
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]
        cur = self._cursor()
        try:
            cur.execute(
                'INSERT INTO staging_run (run_id, source, source_file, item_count, held, hold_reasons) '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                (run_id, source, source_file, len(items), bool(hold_reasons), _dumps(hold_reasons or None)),
            )
            for item, verdict in zip(items, verdicts):
                self._upsert_item(cur, run_id, item, verdict)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return run_id

    def _upsert_item(self, cur, run_id: str, item: dict, verdict: Verdict) -> None:
        source_url = verdict.normalized['source_url'] or item.get('source_url') or ''
        if not source_url:
            # source_url 이 없으면 unique 키를 못 잡는다. 런마다 새 행으로 남긴다
            source_url = f'missing:{run_id}:{uuid.uuid4().hex}'

        raw_json = _dumps(item)
        fingerprint = content_fingerprint(item)
        if verdict.status != REJECTED:  # 작품을 특정 못 한 행은 이력에 남기지 않는다
            self._record_source_success(cur, item, verdict.normalized, fingerprint)
        cur.execute('SELECT id, raw FROM works_staging WHERE source_url = %s', (source_url,))
        existing = cur.fetchone()

        # 수집 내용이 그대로면(수집 시각만 다름) 판정·import 상태를 건드리지 않는다.
        # 재크롤링할 때마다 사람 판정이 날아가거나 같은 작품을 BE 로 또 보내지 않게 (#28 변경 감지)
        if existing and content_fingerprint(json.loads(existing['raw'])) == fingerprint:
            cur.execute('UPDATE works_staging SET run_id = %s, raw = %s WHERE id = %s',
                        (run_id, raw_json, existing['id']))
            return

        raw_values = [_raw_text(item.get(c)) for c in RAW_COLUMNS]
        values = (run_id, *raw_values, _dumps(item.get('hashtags')), raw_json,
                  verdict.status, _dumps(verdict.normalized), _dumps(verdict.violations))

        if existing:
            sets = ', '.join(f'{c} = %s' for c in RAW_COLUMNS)
            cur.execute(
                f'UPDATE works_staging SET run_id = %s, {sets}, hashtags = %s, raw = %s, '
                'status = %s, normalized = %s, violations = %s, '
                'llm_verdict = NULL, confidence = NULL, reviewed_by = NULL, reviewed_at = NULL, '
                'import_error = NULL WHERE id = %s',
                (*values, existing['id']),
            )
        else:
            cols = ', '.join(RAW_COLUMNS)
            marks = ', '.join(['%s'] * len(RAW_COLUMNS))
            cur.execute(
                f'INSERT INTO works_staging (run_id, {cols}, hashtags, raw, status, normalized, violations, source_url) '
                f'VALUES (%s, {marks}, %s, %s, %s, %s, %s, %s)',
                (*values, source_url),
            )

    # ------------------------------------------------------------------ works_source (#28)

    def _record_source_success(self, cur, item: dict, normalized: dict, fingerprint: str) -> None:
        """수집에 성공한 작품의 이력을 갱신한다. 제목은 검색어 목록에 더한다 (제목이 바뀌어도 옛 제목으로 찾게)."""
        url = normalized.get('source_url') or ''
        key = platform_work_key(url)
        if not key:
            return
        title = (normalized.get('works_name') or '')[:255]
        cur.execute('SELECT id, search_keywords FROM works_source WHERE platform = %s AND platform_work_id = %s', key)
        row = cur.fetchone()
        keywords = json.loads(row['search_keywords']) if row and row['search_keywords'] else []
        if title and title not in keywords:
            keywords.append(title)
        values = (url, title or None, (normalized.get('artist_name') or '')[:255] or None,
                  (item.get('works_type') or '').strip() or None,  # 웹툰 · 웹소설 (검색 후보 고를 때 그대로 씀)
                  _dumps(keywords), fingerprint)
        if row:
            cur.execute(
                'UPDATE works_source SET source_url = %s, title_snapshot = %s, artist_snapshot = %s, works_type = %s, '
                'search_keywords = %s, fingerprint_hash = %s, last_crawled_at = UTC_TIMESTAMP(), last_success_at = UTC_TIMESTAMP(), '
                'crawl_status = %s, crawl_fail_count = 0, last_error = NULL, relinked_to = NULL WHERE id = %s',
                (*values, SOURCE_SUCCESS, row['id']))
        else:
            cur.execute(
                'INSERT INTO works_source (source_url, title_snapshot, artist_snapshot, works_type, search_keywords, '
                'fingerprint_hash, platform, platform_work_id, last_crawled_at, last_success_at, crawl_status) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP(), %s)',
                (*values, *key, SOURCE_SUCCESS))

    def record_source_failure(self, url: str, status: str, error: str = '', at: datetime | None = None) -> int | None:
        """수집 실패를 이력에 남기고 연속 실패 횟수를 돌려준다. 이력에 없던 링크도 행을 만든다.
        at 이 마지막 수집 시각보다 이르면 이미 반영한 실패라 세지 않는다 (같은 리포트를 다시 읽어도 한 번만).
        MAX_SOURCE_FAILS 번 이상 실패하면 그 링크의 staging 행을 검수 대기로 돌린다."""
        url = canonical_landing_url(url)
        key = platform_work_key(url)
        if not key:
            return None
        # 이력 시각은 UTC (리포트 시각이 UTC)
        at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
        cur = self._cursor()
        cur.execute('SELECT id, last_crawled_at, crawl_fail_count FROM works_source '
                    'WHERE platform = %s AND platform_work_id = %s', key)
        row = cur.fetchone()
        if row and row['last_crawled_at'] and row['last_crawled_at'] >= at.replace(microsecond=0):
            return row['crawl_fail_count']
        if row:
            cur.execute('UPDATE works_source SET last_crawled_at = %s, crawl_status = %s, '
                        'crawl_fail_count = crawl_fail_count + 1, last_error = %s WHERE id = %s',
                        (at, status, error[:2000] or None, row['id']))
            fails = row['crawl_fail_count'] + 1
        else:
            cur.execute('INSERT INTO works_source (platform, platform_work_id, source_url, last_crawled_at, '
                        'crawl_status, crawl_fail_count, last_error) VALUES (%s, %s, %s, %s, %s, 1, %s)',
                        (*key, url, at, status, error[:2000] or None))
            fails = 1
        self._conn.commit()
        if fails >= MAX_SOURCE_FAILS:
            self._link_broken_to_review(url, status, fails)
        return fails

    def _link_broken_to_review(self, url: str, status: str, fails: int) -> None:
        cur = self._cursor()
        cur.execute('SELECT id, status FROM works_staging WHERE source_url = %s', (url,))
        row = cur.fetchone()
        if row and row['status'] not in (NEEDS_REVIEW, REJECTED):
            self._append_violation(row['id'], NEEDS_REVIEW, {
                'field': 'source_url', 'code': 'LINK_BROKEN', 'value': f'{status} {fails}회', 'severity': NEEDS_REVIEW})

    def mark_source_relinked(self, url: str, new_url: str) -> None:
        key = platform_work_key(canonical_landing_url(url))
        if not key:
            return
        cur = self._cursor()
        cur.execute('UPDATE works_source SET crawl_status = %s, relinked_to = %s, last_crawled_at = UTC_TIMESTAMP() '
                    'WHERE platform = %s AND platform_work_id = %s', (SOURCE_RELINKED, new_url, *key))
        self._conn.commit()

    def broken_sources(self, platform: str | None = None, limit: int = 100) -> list[dict]:
        """링크 복구 대상: 마지막 수집이 실패한 작품. 실패가 적은(최근 깨진) 것부터."""
        where, params = 'crawl_status NOT IN (%s, %s)', [SOURCE_SUCCESS, SOURCE_RELINKED]
        if platform:
            where += ' AND platform = %s'
            params.append(platform)
        cur = self._cursor()
        cur.execute(f'SELECT * FROM works_source WHERE {where} ORDER BY crawl_fail_count, id LIMIT %s',
                    (*params, limit))
        return [_loads(r, 'search_keywords') for r in cur.fetchall()]

    # 같은 링크의 staging 행(검수 상태)을 붙여 돌려준다. 검수 대기(LINK_BROKEN)가 왜 생겼는지 같이 보려고
    _SOURCE_SELECT = ('SELECT ws.*, s.id AS staging_id, s.status AS staging_status FROM works_source ws '
                      'LEFT JOIN works_staging s ON s.source_url = ws.source_url')

    def list_sources(self, status: str | None = None, platform: str | None = None, works_id: int | None = None,
                     limit: int = 50, offset: int = 0) -> list[dict]:
        """수집 이력 목록. status='broken' 이면 마지막 수집이 실패한 것(복구 대상), 그 밖에는 상태 코드 그대로."""
        where, params = [], []
        if status == 'broken':
            where.append('ws.crawl_status NOT IN (%s, %s)')
            params += [SOURCE_SUCCESS, SOURCE_RELINKED]
        elif status:
            where.append('ws.crawl_status = %s')
            params.append(status)
        if platform:
            where.append('ws.platform = %s')
            params.append(platform)
        if works_id:
            where.append('ws.works_id = %s')
            params.append(works_id)
        sql = self._SOURCE_SELECT + (' WHERE ' + ' AND '.join(where) if where else '')
        cur = self._cursor()
        cur.execute(sql + ' ORDER BY ws.crawl_fail_count DESC, ws.updated_at DESC, ws.id DESC LIMIT %s OFFSET %s',
                    (*params, limit, offset))
        return [_loads(r, 'search_keywords') for r in cur.fetchall()]

    def get_source_by_key(self, platform: str, platform_work_id: str) -> dict | None:
        cur = self._cursor()
        cur.execute(self._SOURCE_SELECT + ' WHERE ws.platform = %s AND ws.platform_work_id = %s',
                    (platform, platform_work_id))
        row = cur.fetchone()
        return _loads(row, 'search_keywords') if row else None

    def get_source(self, url: str) -> dict | None:
        key = platform_work_key(canonical_landing_url(url))
        if not key:
            return None
        cur = self._cursor()
        cur.execute('SELECT * FROM works_source WHERE platform = %s AND platform_work_id = %s', key)
        row = cur.fetchone()
        return _loads(row, 'search_keywords') if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        cur = self._cursor()
        cur.execute('SELECT * FROM staging_run ORDER BY created_at DESC LIMIT %s', (limit,))
        return [_loads(r, 'hold_reasons') for r in cur.fetchall()]

    def release_run(self, run_id: str, released_by: str) -> bool:
        cur = self._cursor()
        cur.execute('UPDATE staging_run SET held = FALSE, released_by = %s WHERE run_id = %s AND held',
                    (released_by, run_id))
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ review

    def get(self, staging_id: int) -> dict | None:
        cur = self._cursor()
        cur.execute('SELECT * FROM works_staging WHERE id = %s', (staging_id,))
        row = cur.fetchone()
        return _loads(row, 'raw', 'hashtags', 'normalized', 'violations', 'llm_verdict') if row else None

    def queue(self, status: str = NEEDS_REVIEW, limit: int = 50, offset: int = 0) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            'SELECT id, run_id, source_url, works_name, artist_name, platform, genre, age_classification, '
            'works_type, thumbnail_url, status, normalized, violations '
            'FROM works_staging WHERE status = %s ORDER BY id LIMIT %s OFFSET %s',
            (status, limit, offset),
        )
        return [_loads(r, 'normalized', 'violations') for r in cur.fetchall()]

    def save_review(self, staging_id: int, status: str, normalized: dict, violations: list[dict],
                    reviewer: str, decisions: list[tuple[str, str | None, str | None]]) -> None:
        """사람 판정 저장. decisions 는 (field, raw_value, decided_value) — Layer 3 학습 재료."""
        cur = self._cursor()
        try:
            cur.execute(
                'UPDATE works_staging SET status = %s, normalized = %s, violations = %s, '
                'reviewed_by = %s, reviewed_at = NOW() WHERE id = %s',
                (status, _dumps(normalized), _dumps(violations), reviewer, staging_id),
            )
            for field_name, raw_value, decided_value in decisions:
                cur.execute(
                    'INSERT INTO review_decision (staging_id, field, raw_value, decided_value, decided_by) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    (staging_id, field_name, raw_value, decided_value, reviewer),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ import

    def _importable_where(self, run_id: str | None) -> tuple[str, tuple]:
        marks = ', '.join(['%s'] * len(IMPORTABLE))
        where = f'WHERE s.status IN ({marks}) AND NOT r.held'
        params: tuple = IMPORTABLE
        if run_id:
            where += ' AND s.run_id = %s'
            params = (*params, run_id)
        return where, params

    def importable(self, limit: int = 500, run_id: str | None = None) -> list[dict]:
        """보류되지 않은 런의 AUTO_PASS / APPROVED 건. run_id 를 주면 그 런만."""
        where, params = self._importable_where(run_id)
        cur = self._cursor()
        cur.execute(
            f'SELECT s.id, s.normalized FROM works_staging s '
            f'JOIN staging_run r ON r.run_id = s.run_id {where} ORDER BY s.id LIMIT %s',
            (*params, limit),
        )
        return [_loads(r, 'normalized') for r in cur.fetchall()]

    def importable_summary(self, run_id: str | None = None) -> list[dict]:
        """import 전에 사람이 확인할 런별 건수. 엉뚱한 런이 섞여 나가는 걸 막는다."""
        where, params = self._importable_where(run_id)
        cur = self._cursor()
        cur.execute(
            f'SELECT r.run_id, r.source, r.source_file, COUNT(*) AS n FROM works_staging s '
            f'JOIN staging_run r ON r.run_id = s.run_id {where} '
            f'GROUP BY r.run_id, r.source, r.source_file ORDER BY r.run_id',
            params,
        )
        return cur.fetchall()

    def mark_imported(self, staging_id: int, works_id: int | None) -> None:
        cur = self._cursor()
        cur.execute('UPDATE works_staging SET status = %s, imported_works_id = %s, import_error = NULL '
                    'WHERE id = %s', (IMPORTED, works_id, staging_id))
        if works_id:
            # 수집 이력에 BE 작품을 연결한다
            cur.execute('UPDATE works_source ws JOIN works_staging s ON s.source_url = ws.source_url '
                        'SET ws.works_id = %s WHERE s.id = %s', (works_id, staging_id))
        self._conn.commit()

    def _append_violation(self, staging_id: int, status: str, violation: dict) -> None:
        cur = self._cursor()
        cur.execute('SELECT violations FROM works_staging WHERE id = %s', (staging_id,))
        row = cur.fetchone()
        current = json.loads(row['violations']) if row and row['violations'] else []
        cur.execute('UPDATE works_staging SET status = %s, violations = %s, import_error = NULL WHERE id = %s',
                    (status, _dumps(current + [violation]), staging_id))
        self._conn.commit()

    def mark_suspected(self, staging_id: int, candidates: list[int]) -> None:
        """BE 가 기존 작품과 중복 의심이라 만들지 않았다 → 검수 대기로 돌려 사람이 판단한다."""
        self._append_violation(staging_id, NEEDS_REVIEW, {
            'field': None, 'code': 'SUSPECTED_DUPLICATE', 'value': candidates, 'severity': NEEDS_REVIEW})

    def mark_create_needs_value(self, staging_id: int, error: str) -> None:
        """기존 작품이 없어 새로 만들어야 하는데 연령 · 장르 등이 비어 BE 가 만들지 않았다 → 검수 대기."""
        self._append_violation(staging_id, NEEDS_REVIEW, {
            'field': None, 'code': 'CREATE_NEEDS_VALUE', 'value': error, 'severity': NEEDS_REVIEW})

    def mark_skipped(self, staging_id: int, candidates: list[int]) -> None:
        self._append_violation(staging_id, SKIPPED, {
            'field': None, 'code': 'SKIPPED_EXISTING_WEBNOVEL', 'value': candidates, 'severity': SKIPPED})

    def mark_import_failed(self, staging_id: int, error: str) -> None:
        # status 는 그대로 둔다. 다음 import 때 다시 시도된다
        cur = self._cursor()
        cur.execute('UPDATE works_staging SET import_error = %s WHERE id = %s', (error[:2000], staging_id))
        self._conn.commit()

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        cur = self._cursor()
        cur.execute('SELECT status, COUNT(*) AS n FROM works_staging GROUP BY status')
        by_status = {r['status']: r['n'] for r in cur.fetchall()}
        cur.execute('SELECT COUNT(*) AS n FROM works_staging WHERE import_error IS NOT NULL')
        import_errors = cur.fetchone()['n']
        cur.execute('SELECT COUNT(*) AS n FROM review_decision')
        decisions = cur.fetchone()['n']

        # 자동화율 = 사람 손을 안 거친 비율. 판정이 끝난 건(REJECTED 제외)을 분모로 둔다
        auto = by_status.get(AUTO_PASS, 0)
        cur.execute('SELECT COUNT(*) AS n FROM works_staging WHERE status = %s AND reviewed_by IS NULL',
                    (IMPORTED,))
        auto += cur.fetchone()['n']
        decided = sum(n for s, n in by_status.items() if s != REJECTED)
        return {
            'by_status': by_status,
            'automation_rate': round(auto / decided, 3) if decided else None,
            'import_errors': import_errors,
            'review_decisions': decisions,
        }


def _raw_text(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
