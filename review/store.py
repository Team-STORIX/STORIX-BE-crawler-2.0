"""works_staging 저장소.

서비스 DB 와 같은 MySQL 인스턴스지만 DB 를 분리한다(STAGING_DATABASE_NAME, 기본 storix_staging).
BE 의 ddl-auto / Flyway baseline 과 섞이지 않게 하려는 것이다.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import mysql.connector

from config import MYSQL_CONFIG
from review.rules import AUTO_PASS, NEEDS_REVIEW, REJECTED, Verdict

APPROVED = 'APPROVED'
IMPORTED = 'IMPORTED'
SKIPPED = 'SKIPPED'   # BE 가 만들지 않기로 한 건 (단행본인데 같은 웹소설이 이미 있음). 다시 보내지 않는다
IMPORTABLE = (AUTO_PASS, APPROVED)

STAGING_DATABASE = os.getenv('STAGING_DATABASE_NAME', 'storix_staging')
SCHEMA_FILE = Path(__file__).with_name('schema.sql')

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
        cur.execute('SELECT id, raw FROM works_staging WHERE source_url = %s', (source_url,))
        existing = cur.fetchone()

        # raw 가 그대로면 판정·import 상태를 건드리지 않는다.
        # 재크롤링할 때마다 사람 판정이 날아가거나 같은 작품을 또 import 하지 않게
        if existing and json.loads(existing['raw']) == item:
            cur.execute('UPDATE works_staging SET run_id = %s WHERE id = %s', (run_id, existing['id']))
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
