"""검수 통과분을 BE 로 승격한다.

Works 에는 BE 만 쓴다. 크롤러가 raw SQL 로 직접 넣던 방식(modules/db_handler.save_one_row)을 대체한다.
BE 를 거치면 enum Converter 를 반드시 타고, ES 색인 이벤트도 같이 나간다.

BE: POST /api/v1/admin/works/import (ADMIN)
  요청 {"items": [{stagingId, worksName, ..., genre: "FANTASY", ...}]}  ← enum 은 name
  응답 result: [{stagingId, result: CREATED|UPDATED|UNCHANGED|FAILED, worksId, error}]
  다른 import 가 돌고 있으면 409 (요청 전체 거절). 이때는 남은 청크도 보내지 않고 멈춘다
"""
import json
import urllib.error
import urllib.request

from review.store import StagingStore

IMPORT_PATH = '/api/v1/admin/works/import'
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
    return item


class BackendClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self._url = base_url.rstrip('/') + IMPORT_PATH
        self._token = token
        self._timeout = timeout

    def import_works(self, items: list[dict]) -> list[dict]:
        req = urllib.request.Request(
            self._url,
            data=json.dumps({'items': items}, ensure_ascii=False).encode('utf-8'),
            method='POST',
            headers={
                'Authorization': f'Bearer {self._token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        return body.get('result', body)


def run_import(store: StagingStore, client: BackendClient, limit: int = 500) -> dict:
    rows = store.importable(limit)
    summary = {'requested': len(rows), 'imported': 0, 'failed': 0, 'locked': False}

    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        payload = [to_request_item(r['id'], r['normalized']) for r in chunk]
        try:
            results = client.import_works(payload)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                # 다른 import 가 진행 중. 실패로 남기지 않고 다음 실행 때 그대로 다시 보낸다
                summary['locked'] = True
                break
            for r in chunk:
                store.mark_import_failed(r['id'], f'BE 호출 실패: HTTP {e.code}')
            summary['failed'] += len(chunk)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            # 청크 단위 실패(BE 다운 등). 건별 결과가 없으니 청크 전체를 실패로 남긴다
            for r in chunk:
                store.mark_import_failed(r['id'], f'BE 호출 실패: {e}')
            summary['failed'] += len(chunk)
            continue

        returned = {res.get('stagingId'): res for res in results}
        for r in chunk:
            res = returned.get(r['id'])
            if res and res.get('result') in ('CREATED', 'UPDATED', 'UNCHANGED'):
                store.mark_imported(r['id'], res.get('worksId'))
                summary['imported'] += 1
            else:
                error = (res or {}).get('error') or 'BE 응답에 결과 없음'
                store.mark_import_failed(r['id'], error)
                summary['failed'] += 1

    return summary
