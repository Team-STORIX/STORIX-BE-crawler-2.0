"""검수 통과분을 BE 로 승격한다.

Works 에는 BE 만 쓴다. 크롤러가 raw SQL 로 직접 넣던 방식(modules/db_handler.save_one_row)을 대체한다.
BE 를 거치면 enum Converter 를 반드시 타고, ES 색인 이벤트도 같이 나간다.

BE: POST /internal/v1/works/import (X-Internal-Api-Key)
  요청 {"items": [{stagingId, worksName, ..., genre: "FANTASY", ...}]}  ← enum 은 name
  응답 result: [{stagingId, result, worksId, candidateWorksIds, error}]
    CREATED | UPDATED | UNCHANGED  → IMPORTED
    SUSPECTED_DUPLICATE            → 검수 대기로 되돌림 (candidateWorksIds 기록, 사람이 붙이거나 새로 만들게 결정)
    SKIPPED                        → 단행본인데 같은 웹소설이 이미 있음. 종결, 다시 보내지 않음
    FAILED                         → import_error 기록, 다음 실행 때 재시도
  사람이 승인할 때 고른 targetWorksId(기존 작품에 붙임) / createNew(새로 만듦)를 함께 보낸다
  다른 import 가 돌고 있으면 409 (요청 전체 거절). 이때는 남은 청크도 보내지 않고 멈춘다
"""
import logging
import re
import urllib.error

from config import OUTPUT_DIR
from modules.logger import get_logger
from review.backend import BackendSession
from review.store import StagingStore

# 어떤 작품을 만들고 건드렸는지 나중에 찾을 수 있게 건별 결과를 남긴다 (stdout + output/logs/stage_import.log)
LOG_FILE = OUTPUT_DIR / 'logs' / 'stage_import.log'
log = get_logger('stage_import')


def _ensure_file_log() -> None:
    if any(isinstance(h, logging.FileHandler) for h in log.handlers):
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    log.addHandler(handler)


def _label(row: dict) -> str:
    n = row.get('normalized') or {}
    return f'"{n.get("works_name") or ""}" / "{n.get("artist_name") or ""}"'

# BE 가 새 작품 생성에 필요한 값(연령 · 장르 · 소개 · 표지)이 비었을 때 주는 error.
# 기존 작품에 붙을 땐 빈 값을 무시하므로, 이 FAILED 는 '새로 만들어야 하는데 값이 없음' 이다
CREATE_NEEDS_VALUE = re.compile(r'(?:ageClassification|genre|description|thumbnailUrl) 값이 비어 있습니다')

IMPORT_PATH = '/internal/v1/works/import'
CHUNK_SIZE = 100

_FIELD_MAP = {
    'works_name': 'worksName',
    'artist_name': 'artistName',
    'author': 'author',
    'illustrator': 'illustrator',
    'original_author': 'originalAuthor',
    'age_classification': 'ageClassification',
    'genre': 'genre',
    'works_type': 'worksType',
    'platform': 'platform',
    'source_url': 'landingUrl',
    'description': 'description',
    'thumbnail_url': 'thumbnailUrl',
    'hashtags': 'hashtags',
}


def to_request_item(staging_id: int, normalized: dict) -> dict:
    item = {'stagingId': staging_id}
    for src, dst in _FIELD_MAP.items():
        item[dst] = normalized.get(src) or None
    item['hashtags'] = item['hashtags'] or []
    if normalized.get('_target_works_id'):
        item['targetWorksId'] = normalized['_target_works_id']
    if normalized.get('_create_new'):
        item['createNew'] = True
    return item


class BackendClient:
    def __init__(self, session: BackendSession, timeout: float = 60.0):
        self._session = session
        self._timeout = timeout

    def import_works(self, items: list[dict]) -> list[dict]:
        return self._session.request('POST', IMPORT_PATH, {'items': items}, timeout=self._timeout)


def run_import(store: StagingStore, client: BackendClient, limit: int = 500, run_id: str | None = None) -> dict:
    _ensure_file_log()
    rows = store.importable(limit, run_id)
    summary = {'requested': len(rows), 'imported': 0, 'suspected': 0, 'skipped': 0, 'failed': 0,
               'locked': False, 'results': {}}
    log.info('import 시작 base=%s run_id=%s requested=%d',
             getattr(getattr(client, '_session', None), 'base_url', '?'), run_id or 'ALL', len(rows))

    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        payload = [to_request_item(r['id'], r['normalized']) for r in chunk]
        try:
            results = client.import_works(payload)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                # 다른 import 가 진행 중. 실패로 남기지 않고 다음 실행 때 그대로 다시 보낸다
                log.warning('409 다른 import 진행 중 — 중단 (남은 %d건은 다음 실행 때 재전송)', len(rows) - start)
                summary['locked'] = True
                break
            log.error('청크 실패 HTTP %s stagingId=%s', e.code, [r['id'] for r in chunk])
            for r in chunk:
                store.mark_import_failed(r['id'], f'BE 호출 실패: HTTP {e.code}')
            summary['failed'] += len(chunk)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            # 청크 단위 실패(BE 다운 등). 건별 결과가 없으니 청크 전체를 실패로 남긴다
            log.error('청크 실패 %s stagingId=%s', e, [r['id'] for r in chunk])
            for r in chunk:
                store.mark_import_failed(r['id'], f'BE 호출 실패: {e}')
            summary['failed'] += len(chunk)
            continue

        returned = {res.get('stagingId'): res for res in results}
        for r in chunk:
            res = returned.get(r['id'])
            result = (res or {}).get('result') or 'NO_RESULT'
            summary['results'][result] = summary['results'].get(result, 0) + 1
            if result in ('CREATED', 'UPDATED', 'UNCHANGED'):
                store.mark_imported(r['id'], res.get('worksId'))
                summary['imported'] += 1
                log.info('stagingId=%s %s worksId=%s %s', r['id'], result, res.get('worksId'), _label(r))
            elif result == 'SUSPECTED_DUPLICATE':
                candidates = res.get('candidateWorksIds') or []
                store.mark_suspected(r['id'], candidates)
                summary['suspected'] += 1
                log.info('stagingId=%s %s candidates=%s %s', r['id'], result, candidates, _label(r))
            elif result == 'SKIPPED':
                candidates = res.get('candidateWorksIds') or []
                store.mark_skipped(r['id'], candidates)
                summary['skipped'] += 1
                log.info('stagingId=%s %s candidates=%s %s', r['id'], result, candidates, _label(r))
            elif result == 'FAILED' and CREATE_NEEDS_VALUE.search((res or {}).get('error') or ''):
                # 새 작품을 만들어야 하는데 빈 값(연령 · 장르 등)이 있어 BE 가 만들지 않았다 → 사람이 채운다
                store.mark_create_needs_value(r['id'], res['error'])
                summary['needs_value'] = summary.get('needs_value', 0) + 1
                log.info('stagingId=%s %s → 검수 대기 error=%s %s', r['id'], result, res['error'], _label(r))
            else:
                error = (res or {}).get('error') or 'BE 응답에 결과 없음'
                store.mark_import_failed(r['id'], error)
                summary['failed'] += 1
                log.warning('stagingId=%s %s error=%s %s', r['id'], result, error, _label(r))

    log.info('import 끝 %s', summary)
    return summary
