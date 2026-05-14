import json
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SIMILARITY_THRESHOLD = 0.85
_review_lock = threading.Lock()


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
