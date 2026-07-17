import json
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SIMILARITY_THRESHOLD = 0.85
_review_lock = threading.Lock()

_PLATFORM_KEY_MAP = {
    'naver_webtoon': 'NAVER_WEBTOON',
    'naver_novel': 'NAVER_NOVEL',
    'kakao_page': 'KAKAO_PAGE',
    'ridibooks': 'RIDIBOOKS',
    'bomtoon': 'BOMTOON',
    'naver_series': 'NAVER_SERIES',
    # 구버전 JSONL(enum converter 삭제 이전)의 한글 라벨 호환 — 공백 제거 후 조회
    '네이버웹툰': 'NAVER_WEBTOON',
    '네이버웹소설': 'NAVER_NOVEL',
    '네이버시리즈': 'NAVER_SERIES',
    '카카오': 'KAKAO_PAGE',
    '카카오페이지': 'KAKAO_PAGE',
    '리디북스': 'RIDIBOOKS',
    '리디': 'RIDIBOOKS',
    '봄툰': 'BOMTOON',
}


def _extract_work_id(source_url: str) -> str:
    if 'titleId=' in source_url:
        return source_url.split('titleId=')[-1].split('&')[0]
    if '/content/' in source_url:
        return source_url.rstrip('/').split('/')[-1].split('?')[0]
    if '/books/' in source_url:
        return source_url.rstrip('/').split('/books/')[-1].split('?')[0]
    return ''


def try_resolve(record: dict) -> tuple[dict, bool]:
    record = dict(record)

    # 내부 플랫폼 키/한글 라벨 → enum 값 정규화 (구버전 JSONL 호환)
    # '네이버 웹툰'/'네이버웹툰' 같은 공백 표기 차이를 흡수하기 위해 공백 제거 후 조회
    platform_key = (record.get('platform') or '').strip().replace(' ', '')
    if platform_key in _PLATFORM_KEY_MAP:
        record['platform'] = _PLATFORM_KEY_MAP[platform_key]

    if not (record.get('artist_name') or '').strip():
        names = []
        seen = set()
        for field in ('original_author', 'author', 'illustrator'):
            name = (record.get(field) or '').strip()
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        if names:
            record['artist_name'] = ', '.join(names)

    if not record.get('platform_work_id'):
        source = record.get('source_url', '')
        work_id = _extract_work_id(source)
        if work_id:
            record['platform_work_id'] = work_id

    # works_type enum 정규화: 허용값은 '웹툰', '웹소설'만
    _type_map = {'소설': '웹소설', '만화': '웹툰'}
    wt = record.get('works_type', '')
    if wt in _type_map:
        record['works_type'] = _type_map[wt]

    resolvable = bool((record.get('works_name') or '').strip())
    return record, resolvable


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def find_similar_in_db(cursor, works_name: str) -> list[dict]:
    if not works_name:
        return []

    keyword = works_name[:4]
    try:
        cursor.execute(
            "SELECT works_id, works_name FROM works WHERE works_name LIKE %s LIMIT 20",
            (f'%{keyword}%',),
        )
        rows = cursor.fetchall()
        while cursor.nextset():
            pass
    except Exception:
        return []

    results = []
    for row in rows:
        wid, wname = row
        score = _similarity(works_name, wname)
        if score >= SIMILARITY_THRESHOLD:
            results.append({'works_id': wid, 'works_name': wname, 'score': score})

    return sorted(results, key=lambda x: x['score'], reverse=True)


def queue_for_review(record: dict, reason: str | list[str], queue_path: Path) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        'queued_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'reason': reason if isinstance(reason, list) else [reason],
        'record': record,
    }
    with _review_lock:
        with open(queue_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
