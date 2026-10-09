"""BE enum 카탈로그.

enum 목록은 BE 가 소유한다. 파이썬에 복사해 두면 BE enum 이 바뀔 때 어긋난다.
기동 시 BE 의 GET /api/v1/admin/works/enum-catalog 를 받아 메모리에 들고 있는다.
BE 를 못 붙는 로컬 환경에서는 그 응답을 저장한 JSON 파일을 읽는다(--catalog-file).

저장 형태가 enum 마다 다르다 — Platform 은 name(NAVER_WEBTOON), 나머지 셋은 한글 dbValue.
크롤러는 섞어서 뱉으므로 name / dbValue 어느 쪽으로 와도 name 으로 바꾼다.
import API 는 name 으로 받는다.
"""
import json
import urllib.request
from pathlib import Path

CATALOG_PATH = '/api/v1/admin/works/enum-catalog'

# 카탈로그 키 → staging 필드
KINDS = {
    'platform': 'platform',
    'genre': 'genre',
    'ageClassification': 'age_classification',
    'worksType': 'works_type',
}


class EnumCatalog:
    def __init__(self, data: dict):
        # BE 응답: kind → {"storedAs": "name"|"dbValue", "values": [{name, dbValue}, ...]}
        missing = [k for k in KINDS if not (data.get(k) or {}).get('values')]
        if missing:
            raise ValueError(f'enum 카탈로그에 항목이 없음: {", ".join(missing)}')

        # kind → {조회키: name}. name 은 대소문자 무시, dbValue 는 공백만 정리해서 맞춘다
        self._lookup: dict[str, dict[str, str]] = {}
        self._names: dict[str, list[str]] = {}
        self.stored_as: dict[str, str] = {}
        for kind in KINDS:
            table: dict[str, str] = {}
            names: list[str] = []
            self.stored_as[kind] = data[kind].get('storedAs', '')
            for entry in data[kind]['values']:
                name = entry['name']
                names.append(name)
                table[name.upper()] = name
                if entry.get('dbValue'):
                    table[_norm(entry['dbValue'])] = name
            self._lookup[kind] = table
            self._names[kind] = names
        self.raw = data

    def resolve(self, kind: str, value: str | None) -> str | None:
        """name 이나 dbValue 를 name 으로. 못 맞추면 None (매핑은 Layer 2/사람 몫)."""
        v = (value or '').strip()
        if not v:
            return None
        table = self._lookup[kind]
        return table.get(v.upper()) or table.get(_norm(v))

    def names(self, kind: str) -> list[str]:
        return list(self._names[kind])


def _norm(s: str) -> str:
    return ''.join(s.split())


def fetch_catalog(base_url: str, token: str, timeout: float = 10.0) -> EnumCatalog:
    req = urllib.request.Request(
        base_url.rstrip('/') + CATALOG_PATH,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    # STORIX 응답은 CustomResponse 로 감싸져 온다
    return EnumCatalog(body.get('result', body))


def load_catalog_file(path: str | Path) -> EnumCatalog:
    body = json.loads(Path(path).read_text(encoding='utf-8'))
    return EnumCatalog(body.get('result', body))
