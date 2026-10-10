import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR

# 작가 원문을 글 · 그림 · 원작으로 나눈다. 옛 modules/db_handler.py(직접 INSERT 경로, #5 에서 삭제)에서 옮겨 왔다.
# 검수 단계(review/artists.py normalize_artists)가 다시 정규화하므로 여기서는 수집 결과 파일 형식만 맞춘다
_ROLE_TOKEN_RE = re.compile(r'^(?:글|그림|작화|원작|각색)+$')


def parse_artists(artist_name_raw):

    author, illustrator, original_author = None, None, None
    
    if not artist_name_raw:
        return None, None, None

    # 정규화
    text = artist_name_raw.replace('∙', ' ').replace(':', ' ')
    text = text.replace('글/그림', '글 그림').replace('글/원작', '글 원작')

    parts = [p.strip() for p in text.split('/') if p.strip()]

    for part in parts:
        part = part.strip()
        if not part: continue

        # 공백 토큰 단위로 분리: 토큰 '전체'가 역할 키워드일 때만 역할 라벨로 인정한다.
        # ('글쓰는기계' → 이름) 라벨은 이름 앞(카카오식 '글 홍길동')에도,
        # 이름 뒤(네이버식 '홍길동 ∙ 글/그림')에도 올 수 있어 양쪽을 모두 떼어낸다.
        roles: set[str] = set()
        tokens = part.split()

        def _absorb_role(tok: str) -> None:
            if '글' in tok or '각색' in tok:
                roles.add('글')
            if '그림' in tok or '작화' in tok:
                roles.add('그림')
            if '원작' in tok:
                roles.add('원작')

        while tokens and _ROLE_TOKEN_RE.match(tokens[0]):
            _absorb_role(tokens.pop(0))
        while tokens and _ROLE_TOKEN_RE.match(tokens[-1]):
            _absorb_role(tokens.pop())

        name = ' '.join(tokens).strip()
        if not name:
            continue

        if not roles:
            if not author:
                author = name
            elif not illustrator:
                illustrator = name
        else:
            if '글' in roles: author = name
            if '그림' in roles: illustrator = name
            if '원작' in roles: original_author = name

    if len(parts) == 1 and author and not illustrator and not original_author:
        illustrator = author

    return author, illustrator, original_author


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
        elif 'productNo=' in source:
            data['platform_work_id'] = source.split('productNo=')[-1].split('&')[0]

    return {
        'schema_version': '2.0',
        'crawled_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'platform': data.get('platform') or platform,
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
        # 기존 파일이 있으면 로드해서 이전 실행 데이터도 dedup 대상에 포함
        self._records: dict[str, dict] = {}
        if self.path.exists():
            with open(self.path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        self._records[self._record_key(r)] = r
                    except Exception:
                        pass
        print(f'📝 JSONL 출력: {self.path} (기존 {len(self._records)}건 로드)')

    def _record_key(self, record: dict) -> str:
        pid = record.get('platform_work_id', '')
        if pid:
            return f"{record.get('platform', '')}:{pid}"
        return f"{record.get('platform', '')}:{record.get('works_name', '')}"

    def write(self, raw_data: dict) -> None:
        record = _normalize_record(raw_data, self._platform, self._mode)
        key = self._record_key(record)
        with self._lock:
            self._records[key] = record  # 동일 작품이면 덮어씌움

    @property
    def count(self) -> int:
        return len(self._records)

    def close(self) -> None:
        with open(self.path, 'w', encoding='utf-8') as f:
            for record in self._records.values():
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        print(f'✅ JSONL 완료: {len(self._records)}건 → {self.path}')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
