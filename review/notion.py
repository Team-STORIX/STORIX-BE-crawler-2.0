"""노션 '작품 추가 요청' DB (랜딩 폼) 읽기 · 결과 쓰기 (#66).

.env
  NOTION_TOKEN            노션 내부 통합 토큰. 통합을 DB 페이지에 연결(Connections)해야 읽힌다
  NOTION_REQUEST_DB_ID    요청 DB id
  NOTION_REQUEST_MIN_ID   이 ID 부터 처리한다 (기본 441. 그 앞은 사람이 처리한 구간)
"""
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = 'https://api.notion.com/v1'
VERSION = '2022-06-28'

# DB 속성 이름 (노션 폼과 같아야 한다)
P_TITLE = '작품 이름 '  # 폼 제목 칸 이름 끝에 공백이 있다
P_ID = 'ID'
P_TYPES = '작품 형태'
P_PLATFORMS = '연재처'
P_DONE = '추가 여부'        # prod 에 넣었는지
P_REVIEW = '적재 검토 중'   # 사람이 봐야 하는지
P_MEMO = '메모'

DEFAULT_MIN_ID = 441


class NotionError(RuntimeError):
    pass


@dataclass
class Request:
    page_id: str
    id: int
    title: str
    types: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    memo: str = ''


def _text(prop: dict) -> str:
    return ''.join(t.get('plain_text', '') for t in prop.get(prop.get('type'), []) or [])


def parse_page(page: dict) -> Request:
    props = page['properties']
    title_prop = props.get(P_TITLE) or next(p for p in props.values() if p['type'] == 'title')
    return Request(
        page_id=page['id'],
        id=(props.get(P_ID, {}).get('unique_id') or {}).get('number') or 0,
        title=_text(title_prop).strip(),
        types=[o['name'] for o in props.get(P_TYPES, {}).get('multi_select', [])],
        platforms=[o['name'] for o in props.get(P_PLATFORMS, {}).get('multi_select', [])],
        memo=_text(props.get(P_MEMO, {'type': 'rich_text', 'rich_text': []})),
    )


class NotionRequests:
    def __init__(self, token: str | None = None, db_id: str | None = None):
        self.token = token or os.getenv('NOTION_TOKEN', '')
        self.db_id = db_id or os.getenv('NOTION_REQUEST_DB_ID', '')
        if not self.token or not self.db_id:
            raise NotionError('.env 에 NOTION_TOKEN · NOTION_REQUEST_DB_ID 가 없습니다')

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Notion-Version': VERSION,
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = json.loads(e.read() or b'{}').get('message', '')
            if e.code == 404:
                msg += ' (통합이 DB 페이지에 연결됐는지 확인)'
            raise NotionError(f'노션 HTTP {e.code} {msg}') from None

    def pending(self, min_id: int = DEFAULT_MIN_ID) -> list[Request]:
        """아직 처리 안 한 요청 (추가 여부 · 적재 검토 중 둘 다 꺼짐), ID 순."""
        flt = {'and': [
            {'property': P_DONE, 'checkbox': {'equals': False}},
            {'property': P_REVIEW, 'checkbox': {'equals': False}},
            {'property': P_ID, 'unique_id': {'greater_than_or_equal_to': min_id}},
        ]}
        out, cursor = [], None
        while True:
            body = {'filter': flt, 'page_size': 100, **({'start_cursor': cursor} if cursor else {})}
            d = self._call('POST', f'/databases/{self.db_id}/query', body)
            out += [parse_page(p) for p in d['results']]
            if not d.get('has_more'):
                break
            cursor = d['next_cursor']
        return sorted((r for r in out if r.title), key=lambda r: r.id)

    def mark_done(self, req: Request, note: str = '') -> None:
        props = {P_DONE: {'checkbox': True}}
        if note:
            props[P_MEMO] = _memo(req, note)
        self._call('PATCH', f'/pages/{req.page_id}', {'properties': props})

    def mark_review(self, req: Request, reason: str) -> None:
        self._call('PATCH', f'/pages/{req.page_id}',
                   {'properties': {P_REVIEW: {'checkbox': True}, P_MEMO: _memo(req, reason)}})


def _memo(req: Request, note: str) -> dict:
    # 신청자가 쓴 메모는 지우지 않고 뒤에 붙인다
    text = f'{req.memo}\n[크롤러] {note}' if req.memo else f'[크롤러] {note}'
    return {'rich_text': [{'type': 'text', 'text': {'content': text[:2000]}}]}
