import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_review_lock = threading.Lock()

_PLATFORM_KEY_MAP = {
    'naver_webtoon': 'NAVER_WEBTOON',
    'naver_novel': 'NAVER_NOVEL',
    'kakao_page': 'KAKAO_PAGE',
    'ridibooks': 'RIDIBOOKS',
    'bomtoon': 'BOMTOON',
    'naver_series': 'NAVER_SERIES',
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

    # 내부 플랫폼 키(naver_webtoon 등) → DB enum 값 정규화
    platform_key = (record.get('platform') or '').strip()
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
