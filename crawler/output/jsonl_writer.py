import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from modules.db_handler import parse_artists
from config import OUTPUT_DIR


def _normalize_record(data: dict, platform: str, mode: str) -> dict:
    data = dict(data)

    if not data.get('author') and data.get('artist_name'):
        author, illustrator, original_author = parse_artists(data['artist_name'])
        data['author'] = author or ''
        data['illustrator'] = illustrator or ''
        data['original_author'] = original_author or ''

    names = []
    seen: set = set()
    for field in ('original_author', 'author', 'illustrator'):
        name = (data.get(field) or '').strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    if names:
        data['artist_name'] = ', '.join(names)

    if not data.get('platform_work_id'):
        source = data.get('source_url', '')
        if 'titleId=' in source:
            data['platform_work_id'] = source.split('titleId=')[-1].split('&')[0]
        elif '/content/' in source:
            data['platform_work_id'] = source.rstrip('/').split('/')[-1].split('?')[0]
        elif '/books/' in source:
            data['platform_work_id'] = source.rstrip('/').split('/books/')[-1].split('?')[0]

    return {
        'schema_version': '2.0',
        'crawled_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'platform': platform,
        'mode': mode,
        'works_name': data.get('works_name', ''),
        'platform_work_id': data.get('platform_work_id', ''),
        'source_url': data.get('source_url', ''),
        'artist_name': data.get('artist_name', ''),
        'author': data.get('author', ''),
        'illustrator': data.get('illustrator', ''),
        'original_author': data.get('original_author', ''),
        'age_classification': data.get('age_classification', ''),
        'description': data.get('description', ''),
        'genre': data.get('genre', ''),
        'hashtags': data.get('hashtags', []),
        'thumbnail_url': data.get('thumbnail_url', ''),
        'works_type': data.get('works_type', ''),
    }


class JSONLWriter:
    def __init__(self, platform: str, mode: str, output_dir: Path = None, filename: str = None):
        base = output_dir or OUTPUT_DIR
        date_str = datetime.now().strftime('%Y-%m-%d')
        self.path = Path(base) / date_str / (filename or f'{mode}.jsonl')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._platform = platform
        self._mode = mode
        self._lock = threading.Lock()
        self._f = open(self.path, 'a', encoding='utf-8')
        self._count = 0
        print(f'📝 JSONL 출력: {self.path}')

    def write(self, raw_data: dict) -> None:
        record = _normalize_record(raw_data, self._platform, self._mode)
        with self._lock:
            self._f.write(json.dumps(record, ensure_ascii=False) + '\n')
            self._f.flush()
            self._count += 1

    @property
    def count(self) -> int:
        return self._count

    def close(self) -> None:
        self._f.close()
        print(f'✅ JSONL 완료: {self._count}건 → {self.path}')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
